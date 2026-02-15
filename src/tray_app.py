import os
import shutil
import subprocess

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Gdk, GdkPixbuf
import cairo

from config.defaults import TRAY_TOOLTIP, TERMINAL_GEOMETRY
from src.agent_manager import AgentManager
from src.session_monitor import SessionMonitor
from src.session_state import SessionInfo, SessionState
from src.split_view import open_split

# Icon colors
_COLOR_IDLE = (0.6, 0.6, 0.6)       # grey
_COLOR_WORKING = (0.2, 0.5, 1.0)    # blue
_COLOR_SESSIONS = (1.0, 0.8, 0.0)   # yellow — sessions exist, none working
_ICON_SIZE = 22


def _make_icon(border_color: tuple[float, float, float] | None = None) -> GdkPixbuf.Pixbuf:
    """Draw a small terminal icon with optional colored border."""
    size = _ICON_SIZE
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    # Border / background
    if border_color:
        r, g, b = border_color
        ctx.set_source_rgb(r, g, b)
        ctx.rectangle(0, 0, size, size)
        ctx.fill()
        # Inner dark rect
        m = 3
        ctx.set_source_rgb(0.15, 0.15, 0.2)
        ctx.rectangle(m, m, size - 2 * m, size - 2 * m)
        ctx.fill()
    else:
        ctx.set_source_rgb(0.25, 0.25, 0.3)
        ctx.rectangle(0, 0, size, size)
        ctx.fill()

    # Terminal prompt ">_"
    ctx.set_source_rgb(0.4, 0.9, 0.4)
    ctx.select_font_face("monospace", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    ctx.set_font_size(12)
    ctx.move_to(4, 16)
    ctx.show_text(">_")

    # Convert cairo surface to GdkPixbuf
    pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
    return pixbuf


class SessionDashboard:
    """System tray application for managing Claude Code sessions."""

    def __init__(self):
        self.manager = AgentManager()
        self.monitor = SessionMonitor(self.manager, self._on_state_change)

        self._selected: set[str] = set()
        self._current_icon_state = None
        self._previous_states: dict[str, SessionState] = {}
        self._has_notify = shutil.which("notify-send") is not None

        # Pre-render icons
        self._icons = {
            "idle": _make_icon(None),
            "working": _make_icon(_COLOR_WORKING),
            "sessions": _make_icon(_COLOR_SESSIONS),
        }

        # System tray icon
        self.icon = Gtk.StatusIcon()
        self.icon.set_from_pixbuf(self._icons["idle"])
        self.icon.set_tooltip_text(TRAY_TOOLTIP)
        self.icon.connect("popup-menu", self._on_popup)
        self.icon.connect("activate", self._on_activate)
        self.icon.set_visible(True)

        self.menu = Gtk.Menu()
        self._rebuild_menu()
        self._update_icon()
        self.monitor.start()

    # -- Dynamic icon --

    def _update_icon(self) -> None:
        sessions = self.manager.list_sessions()
        has_working = any(s.state == SessionState.WORKING for s in sessions)
        has_sessions = len(sessions) > 0

        if has_working:
            new_state = "working"
        elif has_sessions:
            new_state = "sessions"
        else:
            new_state = "idle"

        if new_state != self._current_icon_state:
            self._current_icon_state = new_state
            self.icon.set_from_pixbuf(self._icons[new_state])

            # Update tooltip
            count = len(sessions)
            if has_working:
                self.icon.set_tooltip_text(f"Agent Dashboard \u2014 {count} session(er) jobbar")
            elif has_sessions:
                self.icon.set_tooltip_text(f"Agent Dashboard \u2014 {count} session(er) v\u00e4ntar")
            else:
                self.icon.set_tooltip_text(TRAY_TOOLTIP)

    # -- Menu building --

    def _rebuild_menu(self) -> None:
        for child in self.menu.get_children():
            self.menu.remove(child)
            child.destroy()

        sessions = self.manager.list_sessions()

        if sessions:
            for session in sessions:
                # Each session gets a submenu
                session_item = Gtk.MenuItem()

                outer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

                # Top row: checkbox + status icon + name + (ttyd url)
                top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                check = Gtk.CheckButton()
                check.set_active(session.tmux_session_name in self._selected)
                check.connect(
                    "toggled", self._on_check_toggled, session.tmux_session_name
                )
                label = Gtk.Label(label=session.display_name)
                label.set_xalign(0)
                top_box.pack_start(check, False, False, 0)
                top_box.pack_start(label, True, True, 0)

                url = self.manager.get_ttyd_url(session.tmux_session_name)
                if url:
                    url_label = Gtk.Label(label=url)
                    url_label.set_opacity(0.6)
                    top_box.pack_end(url_label, False, False, 0)

                outer_box.pack_start(top_box, False, False, 0)

                # Path row: working directory
                if session.working_directory:
                    path_label = Gtk.Label(label=f"     \u21b3 {session.working_directory}")
                    path_label.set_xalign(0)
                    path_label.set_opacity(0.5)
                    css = Gtk.CssProvider()
                    css.load_from_data(b"label { font-size: 0.85em; }")
                    path_label.get_style_context().add_provider(
                        css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
                    )
                    outer_box.pack_start(path_label, False, False, 0)

                session_item.add(outer_box)

                # Submenu per session: Open / Kill
                sub = Gtk.Menu()

                open_item = Gtk.MenuItem(label="\u25b6 \u00d6ppna terminal")
                open_item.connect("activate", self._on_session_click, session)
                sub.append(open_item)

                kill_item = Gtk.MenuItem(label="\u2716 Avsluta session")
                kill_item.connect("activate", self._on_kill_session, session)
                sub.append(kill_item)

                session_item.set_submenu(sub)
                self.menu.append(session_item)

            self.menu.append(Gtk.SeparatorMenuItem())

            # Split view
            show_all = Gtk.MenuItem(label="Visa alla (split)")
            show_all.connect("activate", self._on_show_all)
            self.menu.append(show_all)

            if self._selected:
                count = len(self._selected)
                show_sel = Gtk.MenuItem(label=f"Visa markerade ({count})")
                show_sel.connect("activate", self._on_show_selected)
                self.menu.append(show_sel)

            self.menu.append(Gtk.SeparatorMenuItem())

            # ttyd submenu
            ttyd_menu = Gtk.MenuItem(label="Remote (ttyd)")
            ttyd_sub = Gtk.Menu()
            for session in sessions:
                if session.ttyd_pid:
                    item = Gtk.MenuItem(label=f"Stoppa ttyd: {session.name}")
                    item.connect("activate", self._on_stop_ttyd, session.tmux_session_name)
                else:
                    item = Gtk.MenuItem(label=f"Starta ttyd: {session.name}")
                    item.connect("activate", self._on_start_ttyd, session.tmux_session_name)
                ttyd_sub.append(item)
            ttyd_menu.set_submenu(ttyd_sub)
            self.menu.append(ttyd_menu)

            self.menu.append(Gtk.SeparatorMenuItem())

            stop_all = Gtk.MenuItem(label="Stoppa alla sessioner")
            stop_all.connect("activate", self._on_stop_all)
            self.menu.append(stop_all)
        else:
            empty = Gtk.MenuItem(label="Inga aktiva sessioner")
            empty.set_sensitive(False)
            self.menu.append(empty)

        self.menu.append(Gtk.SeparatorMenuItem())

        web_item = Gtk.MenuItem(label="🌐 Visa i webbläsaren")
        web_item.connect("activate", self._on_open_web_dashboard)
        self.menu.append(web_item)

        self.menu.append(Gtk.SeparatorMenuItem())

        new_item = Gtk.MenuItem(label="Ny session...")
        new_item.connect("activate", self._on_new_session)
        self.menu.append(new_item)

        quit_item = Gtk.MenuItem(label="Avsluta dashboard")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)

        self.menu.show_all()
        self._update_icon()

    # -- Event handlers --

    def _on_popup(self, icon, button, activate_time):
        self._rebuild_menu()
        self.menu.popup(None, None, None, None, button, activate_time)

    def _on_activate(self, icon):
        self._rebuild_menu()
        self.menu.popup(None, None, None, None, 0, Gtk.get_current_event_time())

    def _on_session_click(self, menu_item, session: SessionInfo):
        if not self.manager.session_exists(session.tmux_session_name):
            self._show_error("Sessionen finns inte l\u00e4ngre.")
            GLib.idle_add(self._rebuild_menu)
            return
        subprocess.Popen([
            "xfce4-terminal",
            f"--title=Session: {session.name}",
            f"--geometry={TERMINAL_GEOMETRY}",
            "--execute", "tmux", "attach-session", "-t",
            session.tmux_session_name,
        ])

    def _on_kill_session(self, menu_item, session: SessionInfo):
        self.manager.kill_session(session.tmux_session_name)
        self._selected.discard(session.tmux_session_name)
        GLib.idle_add(self._rebuild_menu)

    def _on_check_toggled(self, check_button, session_name: str):
        if check_button.get_active():
            self._selected.add(session_name)
        else:
            self._selected.discard(session_name)

    def _on_show_all(self, _):
        names = [s.tmux_session_name for s in self.manager.list_sessions() if s.is_alive]
        if names:
            open_split(names)

    def _on_show_selected(self, _):
        alive = {s.tmux_session_name for s in self.manager.list_sessions() if s.is_alive}
        names = [n for n in self._selected if n in alive]
        if names:
            open_split(names)

    def _on_start_ttyd(self, _, session_name: str):
        port = self.manager.start_ttyd(session_name)
        if port:
            url = self.manager.get_ttyd_url(session_name)
            self._show_info(f"ttyd startad p\u00e5 port {port}", url or f"http://localhost:{port}")
        GLib.idle_add(self._rebuild_menu)

    def _on_stop_ttyd(self, _, session_name: str):
        self.manager.stop_ttyd(session_name)
        GLib.idle_add(self._rebuild_menu)

    def _on_stop_all(self, _):
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Stoppa alla sessioner?",
        )
        dialog.format_secondary_text("Alla Claude Code-sessioner och ttyd-instanser avslutas.")
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.YES:
            self.manager.kill_all_sessions()
            self._selected.clear()
            GLib.idle_add(self._rebuild_menu)

    def _on_new_session(self, _):
        dialog = NewSessionDialog()
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            name = dialog.get_project_name()
            wdir = dialog.get_working_dir()
            if name and wdir:
                self.manager.create_session(name, wdir)
                GLib.idle_add(self._rebuild_menu)
        dialog.destroy()

    def _on_open_web_dashboard(self, _):
        """Start web dashboard if needed and open in browser."""
        dashboard_script = "/tmp/claude-pulse/dashboard.py"
        port = 7685
        try:
            # Check if already running
            result = subprocess.run(
                ["pgrep", "-f", f"dashboard.py.*--port {port}"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                # Start the dashboard server
                subprocess.Popen(
                    ["python3", dashboard_script, "--port", str(port)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                import time
                time.sleep(1)
            # Open browser
            subprocess.Popen(
                ["xdg-open", f"http://127.0.0.1:{port}"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception as e:
            self._show_error(f"Kunde inte öppna webb-dashboard: {e}")

    def _on_quit(self, _):
        self.monitor.stop()
        Gtk.main_quit()

    # -- Monitor callback --

    def _on_state_change(self, session: SessionInfo):
        prev = self._previous_states.get(session.tmux_session_name)
        self._previous_states[session.tmux_session_name] = session.state

        # Notify when a session finishes working
        if prev == SessionState.WORKING and session.state in (
            SessionState.IDLE,
            SessionState.WAITING_INPUT,
        ):
            self._notify(session)

        GLib.idle_add(self._rebuild_menu)

    def _notify(self, session: SessionInfo) -> None:
        if not self._has_notify:
            return
        if session.state == SessionState.WAITING_INPUT:
            title = f"💬 {session.name} väntar på svar"
            body = "Sessionen behöver input."
        else:
            title = f"✅ {session.name} klar"
            body = "Sessionen väntar på nästa kommando."
        try:
            subprocess.Popen(
                ["notify-send", "--urgency=normal",
                 f"--icon=utilities-terminal", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # -- Helpers --

    def _show_error(self, text: str) -> None:
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dialog.run()
        dialog.destroy()

    def _show_info(self, text: str, secondary: str = "") -> None:
        dialog = Gtk.MessageDialog(
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        if secondary:
            dialog.format_secondary_text(secondary)
        dialog.run()
        dialog.destroy()

    # -- Run --

    def run(self) -> None:
        Gtk.main()


class NewSessionDialog(Gtk.Dialog):
    """Dialog for creating a new Claude Code session."""

    def __init__(self):
        super().__init__(title="Ny Claude Code-session", flags=0)
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        self.set_default_size(400, -1)

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_start(15)
        box.set_margin_end(15)
        box.set_margin_top(15)
        box.set_margin_bottom(10)

        box.pack_start(Gtk.Label(label="Projektnamn:", xalign=0), False, False, 0)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_placeholder_text("mitt-projekt")
        box.pack_start(self.name_entry, False, False, 0)

        box.pack_start(Gtk.Label(label="Arbetsmapp:", xalign=0), False, False, 0)
        dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.dir_entry = Gtk.Entry()
        self.dir_entry.set_text(os.path.expanduser("~"))
        dir_box.pack_start(self.dir_entry, True, True, 0)
        browse = Gtk.Button(label="Bl\u00e4ddra...")
        browse.connect("clicked", self._on_browse)
        dir_box.pack_start(browse, False, False, 0)
        box.pack_start(dir_box, False, False, 0)
        self.show_all()

    def _on_browse(self, _):
        chooser = Gtk.FileChooserDialog(
            title="V\u00e4lj arbetsmapp",
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        chooser.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        if chooser.run() == Gtk.ResponseType.OK:
            self.dir_entry.set_text(chooser.get_filename())
        chooser.destroy()

    def get_project_name(self) -> str:
        return self.name_entry.get_text().strip()

    def get_working_dir(self) -> str:
        return self.dir_entry.get_text().strip()
