"""QGraphicsView top-down map in world XZ space.

Scene coordinates ARE world coordinates: an item at (worldX, worldZ) sits at scene (X, Z). That lets
the view transform handle pan/zoom for free and keeps click<->world conversion a single mapToScene.

Interactions:
  * wheel              : zoom about the cursor
  * left-drag Link     : teleport — pins X/Z each tick via the poller hold (Y captured at grab so he
                         slides at constant height); on release the hold clears unless "freeze" is on.
  * left-drag empty    : pan
  * right-drag         : pan
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from ...core.poller import Poller, Snapshot
from ...game.actors import ActorInfo, is_ambient
from .islands import ISLANDS, SECTOR_SIZE, Island

# World Y is up; the top-down map uses world X (horizontal) and world Z (vertical).
_LINK_RADIUS_PX = 7.0  # dot fallback radius in *screen* pixels (constant regardless of zoom)
_FACE_DIAMETER_PX = 30.0  # rendered size of the face marker in screen pixels

_ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
_FACE_FILES = ("link_face.png", "link.png", "link_face.jpg")


def _load_link_face() -> Optional[QPixmap]:
    for name in _FACE_FILES:
        path = _ASSETS_DIR / name
        if path.exists():
            pm = QPixmap(str(path))
            if not pm.isNull():
                return pm
    return None


class MapView(QGraphicsView):
    cursor_moved = Signal(float, float)  # world x, z under cursor
    link_moved = Signal(float, float)  # world x, z of Link
    auto_follow_changed = Signal(bool)  # auto-follow toggled (by button or by user pan)
    focus_requested = Signal(str, float, float)  # island name, world x, z — aim the game camera here
    actor_focus_requested = Signal(object)  # ActorInfo — aim the camera / select in the panel
    orbit_drag_delta = Signal(float, float)  # yaw_delta, pitch_delta (degrees)

    def __init__(self, poller: Poller):
        super().__init__()
        self._poller = poller
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QColor(18, 20, 24))
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setMouseTracking(True)
        # The scene holds no items (everything is painted in drawBackground/drawForeground), so
        # without an explicit sceneRect the scroll range is zero and centerOn()/panning silently
        # no-op. Give it a rect generously larger than the Great Sea (±300k) so the view can scroll.
        self._scene.setSceneRect(-2_000_000, -2_000_000, 4_000_000, 4_000_000)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # World Z grows one way; flip Y so the map reads naturally (tweakable later).
        self.scale(0.05, 0.05)

        self._link_world: Optional[QPointF] = None
        self._link_angle_deg: float = 0.0
        self._have_centered = False
        self._face: Optional[QPixmap] = _load_link_face()
        self.show_grid = True
        self._auto_follow = True
        self._show_islands = True
        self._show_actors = True
        self._show_collision = True
        self._collision_path: Optional[QPainterPath] = None
        self._collision_sig: Optional[tuple] = None
        # Cached raster of the collision layer: re-rendered only on zoom change,
        # when the view leaves the cached region, or when the collision data updates.
        self._collision_pixmap: Optional[QPixmap] = None
        self._collision_pixmap_scene_rect: Optional[QRectF] = None
        self._collision_pixmap_scale: float = 0.0
        self._actors: list[ActorInfo] = []
        self._link_actor: Optional[ActorInfo] = None
        self._hover_actor: Optional[ActorInfo] = None

        self._dragging_link = False
        self._panning = False
        self._pan_last: Optional[QPointF] = None
        self._right_panning = False
        self._right_panning_last = None
        self.freeze = False

        # Orbit drag: when enabled, left-drag rotates the camera orbit (yaw/pitch).
        self._orbit_drag_mode = False
        self._orbit_dragging = False
        self._orbit_drag_last: Optional[QPointF] = None

        # Track whether auto_follow was on before a drag started, so we can restore it.
        self._follow_before_drag = False

    @property
    def auto_follow(self) -> bool:
        return self._auto_follow

    def set_auto_follow(self, on: bool) -> None:
        self._auto_follow = on
        self.auto_follow_changed.emit(on)
        if on and self._link_world is not None:
            self.centerOn(self._link_world)

    # ---- data updates -------------------------------------------------------
    def apply_snapshot(self, snap: Snapshot) -> None:
        if snap.collision is not None:
            self._rebuild_collision_path(snap.collision)
        if snap.link_pos is None:
            return
        self._actors = [a for a in snap.actors if a.name != "Link" and not is_ambient(a.name)]
        self._link_actor = next((a for a in snap.actors if a.name == "Link"), None)
        x, _y, z = snap.link_pos
        self._link_world = QPointF(x, z)
        if snap.link_angle_deg is not None:
            self._link_angle_deg = snap.link_angle_deg
        self.link_moved.emit(x, z)
        if self._auto_follow or not self._have_centered:
            self.centerOn(self._link_world)
            self._have_centered = True
        if not self._dragging_link:
            self.viewport().update()

    def recenter(self) -> None:
        if self._link_world is not None:
            self.centerOn(self._link_world)

    def set_grid_visible(self, visible: bool) -> None:
        self.show_grid = visible
        self.viewport().update()

    def set_islands_visible(self, visible: bool) -> None:
        self._show_islands = visible
        self.viewport().update()

    def set_actors_visible(self, visible: bool) -> None:
        self._show_actors = visible
        self.viewport().update()

    def set_orbit_drag_mode(self, enabled: bool) -> None:
        """Enable/disable orbit drag: left-drag rotates camera yaw/pitch when locked on."""
        self._orbit_drag_mode = enabled

    def set_collision_visible(self, visible: bool) -> None:
        self._show_collision = visible
        self.viewport().update()

    def _rebuild_collision_path(self, meshes: list) -> None:
        """Flatten the collision meshes into one scene-space QPainterPath (world XZ)."""
        # Cheap change detection: same meshes + triangle counts -> keep the cached path.
        sig = tuple((m.bgw_addr, len(m.tris)) for m in meshes)
        if sig == self._collision_sig:
            return
        self._collision_sig = sig
        if not meshes:
            self._collision_path = None
            return
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        for mesh in meshes:
            for x0, z0, x1, z1, x2, z2 in mesh.tris:
                path.moveTo(x0, z0)
                path.lineTo(x1, z1)
                path.lineTo(x2, z2)
                path.closeSubpath()
        self._collision_path = path
        self._collision_pixmap = None  # force re-render of the cached layer
        self.viewport().update()

    _COLLISION_PIXMAP_MARGIN = 1.0  # extra viewports cached on each side (pan headroom)

    def _draw_collision_layer(self, painter: QPainter) -> None:
        """Blit the cached collision raster, re-rendering it only when stale."""
        scale = self.transform().m11()
        if scale <= 0:
            return
        view_scene = self.mapToScene(self.viewport().rect()).boundingRect()
        cached = self._collision_pixmap
        if (
            cached is None
            or scale != self._collision_pixmap_scale
            or not self._collision_pixmap_scene_rect.contains(view_scene)
        ):
            self._render_collision_pixmap(view_scene, scale)
            cached = self._collision_pixmap
        if cached is None:
            return
        painter.save()
        painter.resetTransform()
        top_left = self.mapFromScene(self._collision_pixmap_scene_rect.topLeft())
        painter.drawPixmap(top_left, cached)
        painter.restore()

    def _render_collision_pixmap(self, view_scene: QRectF, scale: float) -> None:
        """Render the collision path into a pixmap covering the view plus margin."""
        m = self._COLLISION_PIXMAP_MARGIN
        region = view_scene.adjusted(
            -view_scene.width() * m,
            -view_scene.height() * m,
            view_scene.width() * m,
            view_scene.height() * m,
        )
        # No point rasterising empty space beyond the geometry itself.
        bounds = self._collision_path.boundingRect()
        region = region.intersected(bounds) if region.intersects(bounds) else QRectF()
        if region.isEmpty():
            self._collision_pixmap = None
            self._collision_pixmap_scene_rect = QRectF()
            self._collision_pixmap_scale = scale
            return
        w = max(1, math.ceil(region.width() * scale))
        h = max(1, math.ceil(region.height() * scale))
        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.scale(scale, scale)
        p.translate(-region.left(), -region.top())
        p.setPen(QPen(QColor(70, 130, 110, 90), 0))
        p.setBrush(QColor(60, 140, 115, 60))
        p.drawPath(self._collision_path)
        p.end()
        self._collision_pixmap = pm
        self._collision_pixmap_scene_rect = region
        self._collision_pixmap_scale = scale

    # ---- painting -----------------------------------------------------------
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawBackground(painter, rect)
        if self._show_collision and self._collision_path is not None:
            self._draw_collision_layer(painter)
        if not self.show_grid:
            return
        # Adaptive grid: pick a world step that renders ~60-120px apart.
        px_per_unit = self.transform().m11()
        if px_per_unit <= 0:
            return
        target_px = 80.0
        raw = target_px / px_per_unit
        step = _nice_step(raw)

        left = int(rect.left() // step) * step
        top = int(rect.top() // step) * step
        minor = QPen(QColor(75, 80, 90), 0)
        axis = QPen(QColor(130, 145, 160), 0)

        x = left
        while x < rect.right():
            painter.setPen(axis if abs(x) < step * 0.5 else minor)
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += step
        y = top
        while y < rect.bottom():
            painter.setPen(axis if abs(y) < step * 0.5 else minor)
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += step

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawForeground(painter, rect)
        self._draw_grid_labels(painter, rect)
        if self._show_islands:
            self._draw_islands(painter, rect)
        if self._show_actors:
            self._draw_actors(painter, rect)
        if self._link_world is None:
            return
        # Draw Link at a constant screen size by working in device pixels.
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        center = self.mapFromScene(self._link_world)
        cx, cy = float(center.x()), float(center.y())

        if self._face is not None:
            r = _FACE_DIAMETER_PX / 2.0
            rect = QRectF(-r, -r, _FACE_DIAMETER_PX, _FACE_DIAMETER_PX)
            painter.translate(cx, cy)
            painter.rotate(self._link_angle_deg + 180.0)
            # Highlight ring (amber while dragging, subtle otherwise).
            ring = QColor(240, 200, 90) if self._dragging_link else QColor(30, 34, 40)
            painter.setPen(QPen(ring, 2.5 if self._dragging_link else 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(rect.adjusted(-1.5, -1.5, 1.5, 1.5))
            # Circular-crop the face into the ring.
            clip = QPainterPath()
            clip.addEllipse(rect)
            painter.setClipPath(clip)
            painter.drawPixmap(rect.toRect(), self._face)
            painter.setClipping(False)
        else:
            painter.setPen(QPen(QColor(20, 20, 20), 1))
            fill = QColor(240, 200, 90) if self._dragging_link else QColor(80, 200, 120)
            painter.setBrush(fill)
            painter.drawEllipse(center, _LINK_RADIUS_PX, _LINK_RADIUS_PX)
            # Small direction indicator from the dot's edge.
            rad = math.radians(self._link_angle_deg + 180.0)
            dx = math.cos(rad) * _LINK_RADIUS_PX * 1.8
            dy = math.sin(rad) * _LINK_RADIUS_PX * 1.8
            painter.setPen(QPen(fill, 2.0))
            painter.drawLine(center, QPointF(cx + dx, cy + dy))
        painter.restore()

    def _draw_grid_labels(self, painter: QPainter, view_rect: QRectF) -> None:
        """Draw the sea-chart grid lines and column/row labels (A-G, 1-7)."""
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        grid_pen = QPen(QColor(60, 65, 75, 120), 1.0)
        label_color = QColor(120, 135, 155)
        font = QFont("Consolas", 10, QFont.Weight.DemiBold)
        painter.setFont(font)

        half = 3.5 * SECTOR_SIZE  # half-span of the 7-sector grid
        grid_left = -half
        grid_right = +half
        grid_top = -half
        grid_bottom = +half

        # --- grid lines (only within the sea-chart rectangle) ---
        visible = view_rect.intersects(QRectF(grid_left, grid_top, grid_right - grid_left, grid_bottom - grid_top))
        if visible:
            painter.setPen(grid_pen)
            for i in range(8):  # 8 lines = 7 sectors
                wx = grid_left + i * SECTOR_SIZE
                if view_rect.left() <= wx <= view_rect.right():
                    sp = self.mapFromScene(QPointF(wx, 0))
                    top_pt = self.mapFromScene(QPointF(wx, grid_top))
                    bot_pt = self.mapFromScene(QPointF(wx, grid_bottom))
                    painter.drawLine(top_pt, bot_pt)
                wz = grid_top + i * SECTOR_SIZE
                if view_rect.top() <= wz <= view_rect.bottom():
                    lft = self.mapFromScene(QPointF(grid_left, wz))
                    rgt = self.mapFromScene(QPointF(grid_right, wz))
                    painter.drawLine(lft, rgt)

        # --- column labels (A-G) along the top edge ---
        for col in range(7):
            letter = chr(ord("A") + col)
            wx = grid_left + (col + 0.5) * SECTOR_SIZE
            wy = grid_top - SECTOR_SIZE * 0.12
            sp = self.mapFromScene(QPointF(wx, wy))
            sx, sy = float(sp.x()), float(sp.y())
            if sx < -30 or sx > self.viewport().width() + 30:
                continue
            painter.setPen(label_color)
            painter.drawText(QPointF(sx, sy), letter)

        # --- row labels (1-7) along the left edge ---
        for row in range(7):
            label = str(row + 1)
            wx = grid_left - SECTOR_SIZE * 0.12
            wy = grid_top + (row + 0.5) * SECTOR_SIZE
            sp = self.mapFromScene(QPointF(wx, wy))
            sx, sy = float(sp.x()), float(sp.y())
            if sy < -10 or sy > self.viewport().height() + 10:
                continue
            painter.setPen(label_color)
            painter.drawText(QPointF(sx, sy), label)

        painter.restore()

    def _draw_islands(self, painter: QPainter, view_rect: QRectF) -> None:
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = painter.fontMetrics()

        dot_r = 3.5
        dot_color = QColor(100, 180, 230)
        label_bg = QColor(0, 0, 0, 160)
        label_fg = QColor(210, 225, 240)

        vw, vh = self.viewport().width(), self.viewport().height()

        for isl in ISLANDS:
            scene_pt = QPointF(isl.world_x, isl.world_z)
            screen = self.mapFromScene(scene_pt)
            sx, sy = float(screen.x()), float(screen.y())
            if sx < -100 or sx > vw + 100 or sy < -60 or sy > vh + 60:
                continue

            # Dot
            painter.setPen(QPen(QColor(20, 30, 40), 1))
            painter.setBrush(dot_color)
            painter.drawEllipse(QPointF(sx, sy), dot_r, dot_r)

            # Label with dark background, centered vertically on the dot
            text_x = sx + dot_r + 5
            tight = fm.tightBoundingRect(isl.name)
            text_y = sy - tight.height() / 2.0 - tight.y()
            bbox = fm.boundingRect(isl.name)
            pad = 3
            bg_rect = QRectF(
                text_x - pad,
                text_y + tight.y() - pad,
                bbox.width() + pad * 2,
                bbox.height() + pad * 2,
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(label_bg)
            painter.drawRoundedRect(bg_rect, 3.0, 3.0)
            painter.setPen(label_fg)
            painter.drawText(QPointF(text_x, text_y), isl.name)

        painter.restore()

    def _draw_actors(self, painter: QPainter, view_rect: QRectF) -> None:
        """Live actor markers. Labels appear when few are on screen or on hover."""
        if not self._actors:
            return
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        vw, vh = self.viewport().width(), self.viewport().height()
        dot_r = 3.0
        dot_color = QColor(235, 170, 80)
        edge = QPen(QColor(50, 35, 15), 1)

        on_screen: list[tuple[float, float, ActorInfo]] = []
        for a in self._actors:
            screen = self.mapFromScene(QPointF(a.pos[0], a.pos[2]))
            sx, sy = float(screen.x()), float(screen.y())
            if sx < -20 or sx > vw + 20 or sy < -20 or sy > vh + 20:
                continue
            on_screen.append((sx, sy, a))

        painter.setPen(edge)
        painter.setBrush(dot_color)
        for sx, sy, _a in on_screen:
            painter.drawEllipse(QPointF(sx, sy), dot_r, dot_r)

        # Labels: everything when the view is uncluttered, otherwise just the hovered one.
        label_all = len(on_screen) <= 25
        hover_addr = self._hover_actor.address if self._hover_actor else None
        font = QFont("Segoe UI", 8)
        painter.setFont(font)
        fm = painter.fontMetrics()
        for sx, sy, a in on_screen:
            if not label_all and a.address != hover_addr:
                continue
            text_x = sx + dot_r + 4
            tight = fm.tightBoundingRect(a.name)
            text_y = sy - tight.height() / 2.0 - tight.y()
            bbox = fm.boundingRect(a.name)
            pad = 2
            bg = QRectF(text_x - pad, text_y + tight.y() - pad,
                        bbox.width() + pad * 2, bbox.height() + pad * 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(bg, 3.0, 3.0)
            painter.setPen(QColor(245, 215, 170))
            painter.drawText(QPointF(text_x, text_y), a.name)
        painter.restore()

    # ---- interaction --------------------------------------------------------
    def wheelEvent(self, event) -> None:  # noqa: N802
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self.scale(factor, factor)
        self.viewport().update()

    def _near_link(self, view_pos) -> bool:
        if self._link_world is None:
            return False
        screen = self.mapFromScene(self._link_world)
        grab = (_FACE_DIAMETER_PX / 2.0 + 4.0) if self._face is not None else _LINK_RADIUS_PX * 2.5
        return (QPointF(view_pos) - QPointF(screen)).manhattanLength() < grab

    def _island_at(self, view_pos) -> Optional[Island]:
        if not self._show_islands:
            return None
        for isl in ISLANDS:
            screen = self.mapFromScene(QPointF(isl.world_x, isl.world_z))
            if (QPointF(view_pos) - QPointF(screen)).manhattanLength() < 14.0:
                return isl
        return None

    def _actor_at(self, view_pos) -> Optional[ActorInfo]:
        if not self._show_actors:
            return None
        best, best_d = None, 14.0
        for a in self._actors:
            screen = self.mapFromScene(QPointF(a.pos[0], a.pos[2]))
            d = (QPointF(view_pos) - QPointF(screen)).manhattanLength()
            if d < best_d:
                best, best_d = a, d
        return best

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            if self._near_link(event.position()) and self._link_actor is not None:
                self._panning = False
                self._pan_last = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.actor_focus_requested.emit(self._link_actor)
                return
            actor = self._actor_at(event.position())
            if actor is not None:
                self._panning = False
                self._pan_last = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.actor_focus_requested.emit(actor)
                return
            isl = self._island_at(event.position())
            if isl is not None:
                # The first click of the double-click started a pan; cancel it.
                self._panning = False
                self._pan_last = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.focus_requested.emit(isl.name, isl.world_x, isl.world_z)
                return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton:
            self._right_panning = True
            self._right_pan_last = event.position()
            if self._auto_follow:
                self._auto_follow = False
                self.auto_follow_changed.emit(False)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton and self._orbit_drag_mode:
            self._orbit_dragging = True
            self._orbit_drag_last = event.position()
            self.setCursor(Qt.CursorShape.CrossCursor)
            return

        if event.button() == Qt.MouseButton.LeftButton and self._near_link(
            event.position()
        ):
            self._dragging_link = True
            self._follow_before_drag = self._auto_follow
            if self._auto_follow:
                self._auto_follow = False
                self.auto_follow_changed.emit(False)
            world = self.mapToScene(event.position().toPoint())
            self._apply_teleport(world)
        elif event.button() == Qt.MouseButton.LeftButton:
            self._panning = True
            self._pan_last = event.position()
            if self._auto_follow:
                self._auto_follow = False
                self.auto_follow_changed.emit(False)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        world = self.mapToScene(event.position().toPoint())
        self.cursor_moved.emit(world.x(), world.y())
        if self._orbit_dragging and self._orbit_drag_last is not None:
            delta = event.position() - self._orbit_drag_last
            self._orbit_drag_last = event.position()
            dx, dy = delta.x(), delta.y()
            yaw_delta = dx * 0.3
            pitch_delta = -dy * 0.3
            max_axis = max(abs(dx), abs(dy)) * 0.3
            current_mag_sq = yaw_delta * yaw_delta + pitch_delta * pitch_delta
            cap = max_axis * 1.5
            if current_mag_sq > cap * cap:
                scale = cap / math.sqrt(current_mag_sq)
                yaw_delta *= scale
                pitch_delta *= scale
            self.orbit_drag_delta.emit(yaw_delta, pitch_delta)
            return
        if self._dragging_link:
            self._apply_teleport(world)
            self.viewport().update()
        elif self._right_panning and self._right_pan_last is not None:
            delta = event.position() - self._right_pan_last
            self._right_pan_last = event.position()
            new_center = self.mapToScene(
                self.viewport().rect().center() - delta.toPoint()
            )
            self.centerOn(new_center)
        elif self._panning and self._pan_last is not None:
            delta = event.position() - self._pan_last
            self._pan_last = event.position()
            new_center = self.mapToScene(
                self.viewport().rect().center() - delta.toPoint()
            )
            self.centerOn(new_center)
        else:
            hover = self._actor_at(event.position())
            if hover is not self._hover_actor:
                self._hover_actor = hover
                self.viewport().update()
            near = (
                hover is not None
                or self._island_at(event.position()) is not None
                or self._near_link(event.position())
            )
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if near else Qt.CursorShape.ArrowCursor
            )
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.RightButton and self._right_panning:
            self._right_panning = False
            self._right_pan_last = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._orbit_dragging:
            self._orbit_dragging = False
            self._orbit_drag_last = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if self._dragging_link:
            self._dragging_link = False
            if not self.freeze:
                self._poller.clear_position_hold()
            elif self._link_world is not None:
                # True freeze: also pin Y where he is now, so he hangs at the drop spot.
                self._poller.freeze_position(self._link_world.x(), self._link_world.y())
            self.viewport().update()
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseReleaseEvent(event)

    # ---- teleport helpers ---------------------------------------------------
    def _apply_teleport(self, world: QPointF) -> None:
        # Pin X/Z to the cursor; keep Y following the live value (None) so Link settles to ground.
        self._poller.set_position_hold(x=world.x(), z=world.y(), y=None)
        self._link_world = world

    def clear_hold_if_not_frozen(self) -> None:
        if not self.freeze:
            self._poller.clear_position_hold()


def _nice_step(raw: float) -> float:
    """Round a raw world distance up to a 1/2/5 x 10^n step."""
    import math

    if raw <= 0:
        return 1.0
    exp = math.floor(math.log10(raw))
    base = 10 ** exp
    for m in (1, 2, 5, 10):
        if raw <= m * base:
            return m * base
    return 10 * base
