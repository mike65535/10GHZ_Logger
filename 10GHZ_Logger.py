import tkinter as tk
from tkinter import messagebox, filedialog, ttk
import csv
import os
import sys
from datetime import datetime
from geopy.distance import geodesic
import uuid

# Define a global frequency dictionary
FREQUENCY_MULTIPLIERS = {
    "10G": 1,
    "24G": 2,
    "47G": 3,
    "75G": 4,
    "122G": 5,
    "134G": 5,
    "241G": 5,
    "LIGHT": 5,
}

MODE_OPTIONS = ["CW", "SSB", "FM", "DIG"]


def normalize_date(value):
    digits = value.replace("-", "").strip()
    if "-" not in value and digits.isdigit() and len(digits) == 8:
        return f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
    return value


def normalize_time(value):
    digits = value.replace(":", "").strip()
    if ":" not in value and digits.isdigit() and len(digits) in (3, 4):
        digits = digits.zfill(4)
        return f"{digits[0:2]}:{digits[2:4]}"
    return value


def ask_open_csv(parent, initialdir=None):
    """Open-file dialog that lists each file's modified date/time and size.

    Tk's built-in dialog on Linux has no details view, so build a simple one
    with a Treeview. Windows and macOS native dialogs already show details.
    """
    if sys.platform in ("win32", "darwin"):
        return filedialog.askopenfilename(
            parent=parent, initialdir=initialdir, filetypes=[("CSV Files", "*.csv")]
        )

    dialog = tk.Toplevel(parent)
    dialog.title("Open Log")
    dialog.geometry("640x400")
    dialog.transient(parent)
    result = {"path": None}
    state = {"dir": os.path.abspath(initialdir or os.getcwd()), "sort": ("modified", True)}

    top = ttk.Frame(dialog, padding=(8, 8, 8, 0))
    top.pack(fill=tk.X)
    ttk.Button(top, text="Up", width=4, command=lambda: show(os.path.dirname(state["dir"]))).pack(side=tk.LEFT)
    dir_label = ttk.Label(top, anchor="w")
    dir_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

    body = ttk.Frame(dialog, padding=8)
    body.pack(fill=tk.BOTH, expand=True)
    tree = ttk.Treeview(body, columns=("modified", "size"), selectmode="browse")
    scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    tree.column("#0", width=320)
    tree.column("modified", width=160, anchor="w")
    tree.column("size", width=90, anchor="e")
    for column, heading in (("#0", "Name"), ("modified", "Modified"), ("size", "Size")):
        tree.heading(column, text=heading, command=lambda c=column: sort_by(c))

    def show(directory):
        try:
            names = os.listdir(directory)
        except OSError as error:
            messagebox.showerror("Open Log", str(error), parent=dialog)
            return
        state["dir"] = directory
        dir_label.config(text=directory)
        tree.delete(*tree.get_children())
        rows = []
        for name in names:
            if name.startswith("."):
                continue
            path = os.path.join(directory, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            is_dir = os.path.isdir(path)
            if not is_dir and not name.lower().endswith(".csv"):
                continue
            rows.append((is_dir, name, info.st_mtime, info.st_size))

        column, descending = state["sort"]
        sort_index = {"#0": 1, "modified": 2, "size": 3}[column]
        rows.sort(
            key=lambda row: row[1].lower() if sort_index == 1 else row[sort_index],
            reverse=descending,
        )
        rows.sort(key=lambda row: not row[0])  # folders first, order kept within each group
        for is_dir, name, mtime, size in rows:
            tree.insert(
                "",
                tk.END,
                iid=name,
                text=f"{name}/" if is_dir else name,
                values=(
                    datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                    "" if is_dir else f"{size / 1024:.1f} KB",
                ),
            )
        children = tree.get_children()
        first_file = next((iid for iid in children if not tree.item(iid, "text").endswith("/")), None)
        if first_file:
            tree.selection_set(first_file)
            tree.focus(first_file)
            tree.see(first_file)

    def sort_by(column):
        current, descending = state["sort"]
        state["sort"] = (column, not descending if column == current else column != "#0")
        show(state["dir"])

    def accept(event=None):
        selection = tree.selection()
        if not selection:
            return
        path = os.path.join(state["dir"], selection[0])
        if os.path.isdir(path):
            show(path)
        else:
            result["path"] = path
            dialog.destroy()

    buttons = ttk.Frame(dialog, padding=(8, 0, 8, 8))
    buttons.pack(fill=tk.X)
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT)
    ttk.Button(buttons, text="Open", command=accept).pack(side=tk.RIGHT, padx=(0, 8))
    tree.bind("<Double-1>", accept)
    tree.bind("<Return>", accept)
    dialog.bind("<Escape>", lambda event: dialog.destroy())

    show(state["dir"])
    dialog.wait_visibility()
    dialog.grab_set()
    tree.focus_set()
    parent.wait_window(dialog)
    return result["path"]


class HamRadioLogger(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Ham Radio Logger")
        self.geometry("800x600")
        self.unsaved_changes = False
        self.entries = []
        self.file_path = None
        self.active_cell_edit = None
        self.sort_column = None  # display-only sort; None = log (chronological) order
        self.sort_descending = False

        # Initialize UI
        self.setup_ui()
        self.update_qso_counts()
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def save_entries(self):
        if self.file_path:
            self.write_to_file(self.file_path)
        else:
            self.save_entries_as()

    def save_entries_as(self):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV Files", "*.csv")]
        )
        if file_path:
            self.file_path = file_path
            self.write_to_file(file_path)

    def write_to_file(self, file_path):
        with open(file_path, "w", newline="") as file:
            writer = csv.DictWriter(
                file, fieldnames=[key for key in self.entries[0].keys() if key != "id"]
            )
            writer.writeheader()
            for entry in self.entries:
                entry_copy = entry.copy()
                entry_copy.pop("id", None)  # Remove 'id' if it exists
                writer.writerow(entry_copy)
        self.unsaved_changes = False

    def load_entries(self):
        file_path = ask_open_csv(
            self, os.path.dirname(self.file_path) if self.file_path else None
        )
        if file_path:
            self.file_path = file_path
            with open(file_path, "r") as file:
                reader = csv.DictReader(file)
                self.entries = [row for row in reader]

        self.recalculate_log()

    def recalculate_log(self):
        # Re-normalize every entry's text fields first, not just
        # freshly-typed ones -- a loaded CSV may have been hand-edited
        # outside the app (wrong case, missing date/time punctuation), and
        # this has to happen before the chronological sort below, otherwise
        # inconsistently-formatted dates/times sort incorrectly.
        for entry in self.entries:
            entry["frequency"] = entry["frequency"].upper()
            entry["mode"] = entry["mode"].upper()
            entry["callsign"] = entry["callsign"].upper()
            entry["sent_grid"] = entry["sent_grid"].upper()
            entry["recv_grid"] = entry["recv_grid"].upper()
            entry["date"] = normalize_date(entry["date"].upper())
            entry["time"] = normalize_time(entry["time"].upper())

        # Standard logging order: chronological by date, then time. This also
        # ensures the "unique" first-contact bonus credits whichever QSO
        # actually happened first, not whichever was entered first.
        self.entries.sort(key=lambda entry: (entry["date"], entry["time"]))

        unique_entries = set()
        seen_contacts = set()
        for entry in self.entries:
            # Calculate distance and distance_points from scratch
            entry["distance"] = self.calculate_distance(
                entry["sent_grid"], entry["recv_grid"]
            )
            entry["distance_points"] = self.calculate_distance_points(
                entry["distance"], entry["frequency"]
            )

            # A dupe (same call, band, and both grids as an earlier contact,
            # chronologically) still gets logged but scores zero points.
            contact_key = (
                entry["frequency"],
                entry["callsign"],
                entry["sent_grid"],
                entry["recv_grid"],
            )
            is_dupe = contact_key in seen_contacts
            seen_contacts.add(contact_key)
            entry["dupe"] = "DUPE" if is_dupe else ""
            if is_dupe:
                entry["distance_points"] = 0

            # Set unique indicator based on frequency and callsign
            unique_key = f"{entry['frequency']}-{entry['callsign']}"
            entry["unique"] = "100" if unique_key not in unique_entries else ""
            unique_entries.add(unique_key)

        self.update_history_tree()
        self.update_totals()

    def setup_ui(self):
        menu_bar = tk.Menu(self)
        self.config(menu=menu_bar)

        file_menu = tk.Menu(menu_bar, tearoff=0)
        file_menu.add_command(label="Save", command=self.save_entries)
        file_menu.add_command(label="Save As...", command=self.save_entries_as)
        file_menu.add_command(label="Load", command=self.load_entries)
        file_menu.add_separator()
        file_menu.add_command(label="Recalculate", command=self.recalculate_log)
        menu_bar.add_cascade(label="File", menu=file_menu)

        self.history_frame = ttk.LabelFrame(self, text="Historical Entries")
        self.history_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = (
            "frequency",
            "mode",
            "date",
            "time",
            "callsign",
            "sent_grid",
            "recv_grid",
            "distance",
            "distance_points",
            "unique",
            "dupe",
        )
        self.history_tree = ttk.Treeview(
            self.history_frame, columns=columns, show="headings", height=10
        )

        column_config = [
            ("frequency", "Freq", 50),
            ("mode", "Mode", 60),
            ("date", "Date", 100),
            ("time", "Time Z", 60),
            ("callsign", "Callsign", 100),
            ("sent_grid", "Sent Grid", 80),
            ("recv_grid", "Rcvd Grid", 80),
            ("distance", "Km", 55),
            ("distance_points", "Points", 60),
            ("unique", "Unique", 60),
            ("dupe", "Dupe", 50),
        ]

        self.column_titles = {}
        for col_id, col_name, col_width in column_config:
            self.column_titles[col_id] = col_name
            self.history_tree.heading(
                col_id, text=col_name, command=lambda c=col_id: self.sort_history_by(c)
            )
            self.history_tree.column(col_id, width=col_width, anchor="center")

        tree_scroll_y = ttk.Scrollbar(
            self.history_frame, orient="vertical", command=self.history_tree.yview
        )
        self.history_tree.configure(yscroll=tree_scroll_y.set)
        tree_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.history_tree.pack(fill=tk.BOTH, expand=True)

        # Right-click context menu
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(
            label="Copy", command=self.copy_selected_entries
        )
        self.context_menu.add_command(label="Paste", command=self.paste_entries)
        self.context_menu.add_separator()
        self.context_menu.add_command(
            label="Delete", command=self.delete_selected_entry
        )
        self.history_tree.bind("<Button-3>", self.show_context_menu)
        self.history_tree.bind("<Double-1>", self.make_cell_editable)
        self.history_tree.bind("<Control-c>", self.copy_selected_entries)
        self.history_tree.bind("<Control-v>", self.paste_entries)

        # Separate context menu for plain text fields (inline cell editor and
        # the Add New Entry fields): cut/copy/paste the field's own text
        # selection, instead of the row-level clipboard behavior above.
        self.entry_context_menu = tk.Menu(self, tearoff=0)
        self.entry_context_menu.add_command(
            label="Cut", command=lambda: self.send_entry_clipboard_event("Cut")
        )
        self.entry_context_menu.add_command(
            label="Copy", command=lambda: self.send_entry_clipboard_event("Copy")
        )
        self.entry_context_menu.add_command(
            label="Paste", command=lambda: self.send_entry_clipboard_event("Paste")
        )

        # Menu used to pick a frequency/mode value when editing those cells
        # in the history log; repopulated on the fly in make_cell_editable.
        self.cell_picker_menu = tk.Menu(self, tearoff=0)

        # Tk's own grab-based auto-dismiss isn't reliable on every platform,
        # so close the context menu manually on any outside click or Escape.
        self.bind_all("<Button-1>", self.dismiss_context_menu, add="+")
        self.bind_all("<Escape>", self.dismiss_context_menu, add="+")

        # Data Entry Frame
        self.entry_frame = ttk.LabelFrame(self, text="Add New Entry")
        self.entry_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        #        frequency_options = ["10 GHz", "24 GHz", "47 GHz", "76 GHz", "123 GHz"]
        frequency_options = list(FREQUENCY_MULTIPLIERS.keys())

        self.frequency_entry = self.create_dropdown(
            self.entry_frame, "Frequency", frequency_options
        )
        self.mode_entry = self.create_dropdown(self.entry_frame, "Mode", MODE_OPTIONS)
        self.date_entry = self.create_entry(self.entry_frame, "Date (YYYY-MM-DD)")
        self.time_entry = self.create_entry(self.entry_frame, "Time (HH:MM)")
        self.callsign_entry = self.create_entry(self.entry_frame, "Callsign")
        self.sent_grid_entry = self.create_entry(self.entry_frame, "Sent Grid")
        self.recv_grid_entry = self.create_entry(self.entry_frame, "Rcvd Grid")

        submit_button = ttk.Button(
            self.entry_frame, text="Enter", command=self.add_entry_to_log
        )
        submit_button.pack(side=tk.LEFT, padx=5, pady=5)

        self.bind("<Return>", lambda event: self.add_entry_to_log())

        # Bottom Frame for Totals
        self.total_frame = ttk.Frame(self, relief=tk.SUNKEN, borderwidth=2)
        self.total_frame.pack(fill=tk.X, padx=5, pady=5)

        # Combined Total Points Label
        self.total_points_label = ttk.Label(self.total_frame, text="Total Points: 0")
        self.total_points_label.pack(side=tk.LEFT, padx=(10, 5))

        # QSO count, total and per band
        self.qso_counts_label = ttk.Label(self.total_frame, text="")
        self.qso_counts_label.pack(side=tk.LEFT, padx=(20, 5))

        # Total Distance Points Label

    #        self.total_distance_label = ttk.Label(
    #            self.total_frame, text="Total Distance Points:"
    #        )
    #        self.total_distance_label.pack(side=tk.LEFT, padx=(10, 5))
    #        self.total_distance_value = ttk.Label(self.total_frame, text="0")
    #        self.total_distance_value.pack(side=tk.LEFT)

    # Total Unique Label
    #        self.total_unique_label = ttk.Label(self.total_frame, text="Total Unique:")
    #        self.total_unique_label.pack(side=tk.LEFT, padx=(20, 5))
    #        self.total_unique_value = ttk.Label(self.total_frame, text="0")
    #        self.total_unique_value.pack(side=tk.LEFT)

    def create_entry(self, parent, label_text):
        frame = ttk.Frame(parent)
        frame.pack(side=tk.LEFT, padx=2, pady=2)
        label = ttk.Label(frame, text=label_text)
        label.pack(side=tk.TOP, anchor="w")
        entry = ttk.Entry(frame, width=10)
        entry.pack(side=tk.TOP)

        # Remove highlight when the entry receives focus
        entry.bind("<FocusIn>", lambda event: entry.selection_clear())
        self.bind_entry_context_menu(entry)

        return entry

    def create_dropdown(self, parent, label_text, options):
        frame = ttk.Frame(parent)
        frame.pack(side=tk.LEFT, padx=2, pady=2)
        label = ttk.Label(frame, text=label_text)
        label.pack(side=tk.TOP, anchor="w")
        combo = ttk.Combobox(frame, values=options, state="readonly", width=8)
        combo.pack(side=tk.TOP)
        combo.set(options[0])
        return combo

    def update_totals(self):
        # Calculate total distance points by summing all valid distance points
        total_distance_points = sum(
            float(entry["distance_points"])
            for entry in self.entries
            if entry["distance_points"]
        )

        # Calculate total unique points by counting entries marked as unique ("100")
        total_unique_points = sum(
            100 for entry in self.entries if entry["unique"] == "100"
        )

        # Calculate combined total points
        combined_total_points = total_distance_points + total_unique_points

        # Update the combined total display label
        self.total_points_label.config(
            text=f"Total Points: {combined_total_points:.2f}"
        )

        self.update_qso_counts()

    def update_qso_counts(self):
        tracked_bands = ("10G", "24G", "47G", "75G")
        band_counts = {band: 0 for band in tracked_bands}
        other_count = 0
        unique_count = 0
        for entry in self.entries:
            if entry["frequency"] in band_counts:
                band_counts[entry["frequency"]] += 1
            else:
                other_count += 1
            if entry["unique"] == "100":
                unique_count += 1

        band_summary = "  ".join(
            f"{band}: {band_counts[band]}" for band in tracked_bands
        )
        self.qso_counts_label.config(
            text=(
                f"QSO Total: {len(self.entries)}  {band_summary}  "
                f"Other: {other_count}     Uniques: {unique_count}"
            )
        )

    def show_context_menu(self, event):
        selected = self.history_tree.identify_row(event.y)
        if selected and selected not in self.history_tree.selection():
            self.history_tree.selection_set(selected)
        self.context_menu.post(event.x_root, event.y_root)

    def dismiss_context_menu(self, event=None):
        if event is not None and event.widget in (
            self.context_menu,
            self.entry_context_menu,
            self.cell_picker_menu,
        ):
            return  # let clicks on the menu's own items invoke their command
        self.context_menu.unpost()
        self.entry_context_menu.unpost()
        self.cell_picker_menu.unpost()

    def show_entry_context_menu(self, event):
        event.widget.focus_set()
        self.entry_context_menu.post(event.x_root, event.y_root)

    def clipboard_action(self, widget, action):
        if widget is None:
            return
        if action == "Paste":
            try:
                clipboard_text = widget.clipboard_get()
            except tk.TclError:
                return
            try:
                widget.delete("sel.first", "sel.last")
            except tk.TclError:
                pass  # no selection to overwrite
            widget.insert("insert", clipboard_text)
        else:  # Copy / Cut
            try:
                selected_text = widget.selection_get()
            except tk.TclError:
                return
            widget.clipboard_clear()
            widget.clipboard_append(selected_text)
            if action == "Cut":
                widget.delete("sel.first", "sel.last")

    def send_entry_clipboard_event(self, action):
        self.clipboard_action(self.focus_get(), action)

    def bind_entry_context_menu(self, widget):
        widget.bind("<Button-3>", self.show_entry_context_menu)
        for key, action in (("c", "Copy"), ("x", "Cut"), ("v", "Paste")):
            widget.bind(
                f"<Control-{key}>",
                lambda event, action=action: self.clipboard_action(
                    event.widget, action
                )
                or "break",
            )

    def make_cell_editable(self, event):
        selected_item = self.history_tree.focus()
        if not selected_item:
            return

        column_index = self.history_tree.identify_column(event.x)
        col_name = self.history_tree["columns"][int(column_index[1:]) - 1]
        cell_value = self.history_tree.item(selected_item, "values")[
            int(column_index[1:]) - 1
        ]

        x, y, width, height = self.history_tree.bbox(selected_item, column_index)

        # Frequency and mode are constrained to fixed option sets elsewhere
        # (the Add New Entry dropdowns); free-text editing here could put in
        # a value that doesn't match any FREQUENCY_MULTIPLIERS key, silently
        # breaking that row's scoring. Offer the same fixed choices instead,
        # as a plain click-to-pick menu (ttk.Combobox's dropdown relies on a
        # Tk grab that isn't reliable in every environment, same issue as the
        # old right-click context menu).
        if col_name in ("frequency", "mode"):
            options = (
                list(FREQUENCY_MULTIPLIERS.keys())
                if col_name == "frequency"
                else MODE_OPTIONS
            )

            def commit_choice(value):
                self.history_tree.set(selected_item, col_name, value)
                self.entries[int(selected_item)][col_name] = value
                self.unsaved_changes = True
                self.recalculate_log()

            self.cell_picker_menu.delete(0, "end")
            for option in options:
                self.cell_picker_menu.add_command(
                    label=option, command=lambda value=option: commit_choice(value)
                )
            root_x = self.history_tree.winfo_rootx() + x
            root_y = self.history_tree.winfo_rooty() + y + height

            # The same double-click also fires our bind_all("<Button-1>")
            # dismiss handler (on the "all" bindtag, right after this
            # widget-level <Double-1> handler) which would immediately
            # unpost a menu posted here. Defer until that dispatch finishes.
            self.after_idle(lambda: self.cell_picker_menu.post(root_x, root_y))
            return

        entry = tk.Entry(self.history_tree, width=width)
        entry.insert(0, cell_value)
        self.bind_entry_context_menu(entry)
        entry.place(x=x, y=y, width=width, height=height)

        def save_edit(event=None):
            new_value = entry.get()
            if col_name == "date":
                new_value = normalize_date(new_value)
            elif col_name == "time":
                new_value = normalize_time(new_value)
            self.history_tree.set(selected_item, col_name, new_value)
            self.entries[int(selected_item)][col_name] = new_value
            entry.destroy()
            self.unsaved_changes = True
            self.active_cell_edit = None
            self.recalculate_log()
            return "break"  # don't let <Return> bubble up to the Add New Entry form

        self.active_cell_edit = save_edit
        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)
        entry.focus()

    def copy_selected_entries(self, event=None):
        selected = self.history_tree.selection()
        if not selected:
            return
        lines = [
            "\t".join(str(v) for v in self.history_tree.item(item, "values"))
            for item in selected
        ]
        self.clipboard_clear()
        self.clipboard_append("\n".join(lines))

    def paste_entries(self, event=None):
        try:
            clipboard_text = self.clipboard_get()
        except tk.TclError:
            return

        for line in clipboard_text.strip("\n").split("\n"):
            fields = line.split("\t")
            if len(fields) < 7:
                continue
            frequency, mode, date, time, callsign, sent_grid, recv_grid = fields[:7]

            # distance, distance_points, unique, and dupe are all derived by
            # recalculate_log() below, so they don't need to be set here.
            self.entries.append(
                {
                    "id": str(uuid.uuid4()),
                    "frequency": frequency,
                    "mode": mode.upper(),
                    "date": normalize_date(date.upper()),
                    "time": normalize_time(time.upper()),
                    "callsign": callsign.upper(),
                    "sent_grid": sent_grid.upper(),
                    "recv_grid": recv_grid.upper(),
                }
            )

        self.unsaved_changes = True
        self.recalculate_log()

    def delete_selected_entry(self):
        selected = self.history_tree.selection()
        if selected:
            # Delete highest index first so earlier deletions don't shift
            # the positions of rows still waiting to be deleted.
            for index in sorted((int(item) for item in selected), reverse=True):
                if index < len(self.entries):
                    del self.entries[index]
                    self.unsaved_changes = True
            self.update_history_tree()
            self.update_totals()

    def add_entry_to_log(self):
        if not all(
            [
                self.frequency_entry.get(),
                self.mode_entry.get(),
                self.date_entry.get(),
                self.time_entry.get(),
                self.callsign_entry.get(),
                self.sent_grid_entry.get(),
                self.recv_grid_entry.get(),
            ]
        ):
            messagebox.showwarning(
                "Incomplete Entry", "Please fill in all fields before submitting."
            )
            return False

        # Standardize frequency format to "GHz"
        #        frequency = self.frequency_entry.get().replace("GHZ", "GHz").upper()
        frequency = self.frequency_entry.get()

        # Auto-correct missing punctuation (e.g. "20261011" -> "2026-10-11",
        # "1234" -> "12:34") and reflect the correction back in the fields.
        date_value = normalize_date(self.date_entry.get().upper())
        time_value = normalize_time(self.time_entry.get().upper())
        self.date_entry.delete(0, tk.END)
        self.date_entry.insert(0, date_value)
        self.time_entry.delete(0, tk.END)
        self.time_entry.insert(0, time_value)

        callsign_value = self.callsign_entry.get().upper()
        sent_grid_value = self.sent_grid_entry.get().upper()
        recv_grid_value = self.recv_grid_entry.get().upper()

        # Calculate distance and distance points
        distance = int(self.calculate_distance(sent_grid_value, recv_grid_value))
        distance_points = self.calculate_distance_points(distance, frequency)

        # A dupe (same call, band, and both grids as a prior contact) still
        # gets logged, but scores zero distance points for the repeat.
        is_dupe = self.is_duplicate_contact(
            frequency, callsign_value, sent_grid_value, recv_grid_value
        )
        if is_dupe:
            distance_points = 0

        entry_data = {
            "id": str(uuid.uuid4()),
            "frequency": frequency,
            "mode": self.mode_entry.get().upper(),
            "date": date_value,
            "time": time_value,
            "callsign": callsign_value,
            "sent_grid": sent_grid_value,
            "recv_grid": recv_grid_value,
            "distance": distance,
            "distance_points": distance_points,
            "unique": self.is_unique_entry(frequency, callsign_value),
            "dupe": "DUPE" if is_dupe else "",
        }
        self.entries.append(entry_data)
        self.unsaved_changes = True
        self.update_history_tree()
        self.update_totals()

        # Clear callsign for the next entry, but leave recv_grid populated
        # since the next contact's grid is often the same as the last one.
        self.callsign_entry.delete(0, tk.END)
        self.time_entry.focus_set()
        return True

    def update_history_tree(self):
        # Clear existing entries in the tree
        self.history_tree.delete(*self.history_tree.get_children())

        # Row iids stay as the entry's index in self.entries (editing and
        # deleting rely on that); a heading sort only changes display order.
        order = list(range(len(self.entries)))
        if self.sort_column:
            numeric = self.sort_column in ("distance", "distance_points", "unique")

            def sort_key(index):
                entry = self.entries[index]
                if self.sort_column in ("date", "time"):
                    # A time is only meaningful alongside its date, so both
                    # headings sort by the full date+time.
                    return (entry["date"], entry["time"])
                value = entry.get(self.sort_column, "")
                if numeric:
                    try:
                        return float(value)
                    except (TypeError, ValueError):
                        return 0.0
                return str(value)

            order.sort(key=sort_key, reverse=self.sort_descending)

        for position, index in enumerate(order):
            entry = self.entries[index]
            row_tag = "even" if position % 2 == 0 else "odd"
            self.history_tree.insert(
                "",
                "end",
                iid=index,
                values=(
                    entry["frequency"],
                    entry["mode"],
                    entry["date"],
                    entry["time"],
                    entry["callsign"],
                    entry["sent_grid"],
                    entry["recv_grid"],
                    int(entry["distance"]),
                    int(entry["distance_points"]),
                    entry["unique"],
                    entry.get("dupe", ""),
                ),
                tags=(row_tag,),
            )

        # In log order, scroll to the bottom to show the most recent entry
        if self.entries:
            self.history_tree.yview_moveto(0.0 if self.sort_column else 1.0)

    def sort_history_by(self, column):
        # Each click on a heading cycles: ascending -> descending -> log order.
        if column != self.sort_column:
            self.sort_column, self.sort_descending = column, False
        elif not self.sort_descending:
            self.sort_descending = True
        else:
            self.sort_column, self.sort_descending = None, False

        for col_id, title in self.column_titles.items():
            if col_id == self.sort_column:
                title += " ▼" if self.sort_descending else " ▲"
            self.history_tree.heading(col_id, text=title)
        self.update_history_tree()

    def calculate_distance(self, sent_grid, recv_grid):
        sent_coords = self.maidenhead_to_latlon(sent_grid)
        recv_coords = self.maidenhead_to_latlon(recv_grid)
        if sent_coords and recv_coords:
            # Return rounded distance as integer
            return round(geodesic(sent_coords, recv_coords).kilometers)
        return 0

    def calculate_distance_points(self, distance, frequency):
        # Standardize frequency and use integer calculations
        multiplier = FREQUENCY_MULTIPLIERS.get(
            frequency, 1
        )  # Default to 1 if frequency is not found
        # Calculate distance points as an integer based on the rounded distance
        return distance * multiplier

    def maidenhead_to_latlon(self, locator):
        if len(locator) < 4:
            return None
        locator = locator.upper()

        lon = (ord(locator[0]) - ord("A")) * 20 - 180
        lon += int(locator[2]) * 2
        lon += 1
        if len(locator) >= 6:
            lon += (ord(locator[4]) - ord("A")) * 5 / 60

        lat = (ord(locator[1]) - ord("A")) * 10 - 90
        lat += int(locator[3])
        lat += 0.5
        if len(locator) >= 6:
            lat += (ord(locator[5]) - ord("A")) * 2.5 / 60

        return lat, lon

    def is_unique_entry(self, frequency, callsign):
        # Standardize frequency format to "GHz" before creating unique key
        # frequency = frequency.replace("GHZ", "GHz").upper()
        unique_key = f"{frequency}-{callsign.upper()}"
        if unique_key in {
            entry["frequency"] + "-" + entry["callsign"] for entry in self.entries
        }:
            return ""
        return "100"

    def is_duplicate_contact(self, frequency, callsign, sent_grid, recv_grid):
        return any(
            entry["frequency"] == frequency
            and entry["callsign"] == callsign
            and entry["sent_grid"] == sent_grid
            and entry["recv_grid"] == recv_grid
            for entry in self.entries
        )

    def has_pending_bottom_entry(self):
        return any(
            entry.get().strip()
            for entry in (
                self.date_entry,
                self.time_entry,
                self.callsign_entry,
                self.sent_grid_entry,
                self.recv_grid_entry,
            )
        )

    def on_close(self):
        if self.active_cell_edit:
            self.active_cell_edit()

        if self.has_pending_bottom_entry():
            if messagebox.askyesno(
                "Unfinished Entry",
                "The Add New Entry form has not been added to the log. "
                "Add it before exiting?",
            ):
                if not self.add_entry_to_log():
                    return  # incomplete entry; stay open so it can be fixed

        if self.unsaved_changes:
            if messagebox.askyesno(
                "Unsaved Changes",
                "You have unsaved changes. Do you want to save before exiting?",
            ):
                self.save_entries()
        self.destroy()


if __name__ == "__main__":
    app = HamRadioLogger()
    app.mainloop()
