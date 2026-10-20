"""Local Windows-friendly Tk interface; conversion runs off the Tk thread."""
from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


BG = "#f3f5f9"
INK = "#14243b"
MUTED = "#607087"
BLUE = "#1765d1"


def _size_text(size: int) -> str:
    value = float(size)
    for suffix in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or suffix == "TB":
            return f"{value:.1f} {suffix}"
        value /= 1024
    return str(size)


class ConverterApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("BLF Converter · MF4 & Excel")
        self.root.geometry("1050x860")
        self.root.minsize(900, 660)
        self.root.configure(background=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.events: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.busy = False
        self.files: dict[str, Path] = {}
        self.bindings: dict[str, tuple[Path, int | None]] = {}
        self.output = tk.StringVar()
        self.mf4 = tk.BooleanVar(value=True)
        self.excel = tk.BooleanVar(value=True)
        self.chunk = tk.StringVar(value="32")
        self.compression = tk.StringVar(value="0 — Nhanh nhất")
        self.dbc_channel = tk.StringVar(value="Tất cả")
        self.status = tk.StringVar(value="Chọn file BLF và DBC để bắt đầu.")
        self.detail = tk.StringVar(value="Xử lý cục bộ trên máy, không tải log lên mạng.")
        self._controls: list[tuple[ttk.Widget, str]] = []
        self._build_style()
        self._build_ui()
        self.root.after(100, self._drain)

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG)
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("Title.TLabel", font=("Segoe UI", 25, "bold"), foreground=INK)
        style.configure("Section.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Muted.TLabel", foreground=MUTED, font=("Segoe UI", 9))
        style.configure("TButton", padding=(12, 6))
        style.configure("Primary.TButton", background=BLUE, foreground="white", font=("Segoe UI", 10, "bold"), padding=(20, 9))
        style.map("Primary.TButton", background=[("active", "#1256b4"), ("disabled", "#9bb8e3")], foreground=[("disabled", "#f2f5fa")])
        style.configure("Treeview", background="white", fieldbackground="white", rowheight=27, borderwidth=0)
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"), background="#e9eef5", padding=5)
        style.map("Treeview", background=[("selected", "#dceaff")], foreground=[("selected", INK)])
        style.configure("Horizontal.TProgressbar", background=BLUE, troughcolor="#e3e9f2", borderwidth=0, thickness=8)
        style.configure("TCheckbutton", background=BG)
        style.configure("TLabelframe", background=BG)
        style.configure("TLabelframe.Label", background=BG, font=("Segoe UI", 10, "bold"))

    def _register(self, widget: ttk.Widget, idle_state: str = "normal") -> ttk.Widget:
        self._controls.append((widget, idle_state))
        return widget

    def _button(self, parent: ttk.Widget, text: str, command, **kwargs) -> ttk.Button:
        return self._register(ttk.Button(parent, text=text, command=command, **kwargs))

    def _tree(self, parent: ttk.Frame, columns: tuple[str, ...], headings: tuple[str, ...], widths: tuple[int, ...], height: int) -> ttk.Treeview:
        panel = ttk.Frame(parent)
        panel.pack(fill="both", expand=True)
        tree = ttk.Treeview(panel, columns=columns, show="headings", height=height, selectmode="extended")
        for key, title, width in zip(columns, headings, widths):
            tree.heading(key, text=title, anchor="w")
            tree.column(key, width=width, minwidth=70, stretch=(key == columns[0]))
        scrollbar = ttk.Scrollbar(panel, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return tree

    def _build_ui(self) -> None:
        # Keep every control reachable on laptops and Windows display scaling.
        page = tk.Canvas(self.root, background=BG, borderwidth=0, highlightthickness=0)
        page_scroll = ttk.Scrollbar(self.root, orient="vertical", command=page.yview)
        page.configure(yscrollcommand=page_scroll.set)
        page_scroll.pack(side="right", fill="y")
        page.pack(side="left", fill="both", expand=True)
        body = ttk.Frame(page, padding=(26, 18, 26, 18))
        body_window = page.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda event: page.configure(scrollregion=page.bbox("all")))
        page.bind("<Configure>", lambda event: page.itemconfigure(body_window, width=event.width))

        def scroll_page(event):
            if isinstance(event.widget, (ttk.Treeview, tk.Text, ttk.Combobox)):
                return
            if event.delta:
                page.yview_scroll(-1 if event.delta > 0 else 1, "units")

        self.root.bind_all("<MouseWheel>", scroll_page)
        header = ttk.Frame(body)
        header.pack(fill="x", pady=(0, 3))
        ttk.Label(header, text="BLF Converter", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="CAN / CAN FD", foreground=BLUE, font=("Segoe UI", 10, "bold")).pack(side="right", pady=(12, 0))
        ttk.Label(body, text="Giải mã bằng DBC  →  MF4 cho MDA 8.6  +  Excel lưu trữ", foreground=MUTED).pack(anchor="w", pady=(0, 14))

        files_head = ttk.Frame(body)
        files_head.pack(fill="x", pady=(0, 6))
        ttk.Label(files_head, text="1   File BLF", style="Section.TLabel").pack(side="left")
        self._button(files_head, "Thêm BLF…", self._add_blf).pack(side="right")
        self._button(files_head, "Xóa đã chọn", self._remove_blf).pack(side="right", padx=(0, 6))
        self.file_tree = self._tree(body, ("file", "size", "status"), ("Tên file", "Dung lượng", "Trạng thái"), (590, 100, 170), 4)
        self.file_tree.tag_configure("done", foreground="#17724b")
        self.file_tree.tag_configure("error", foreground="#bd3039")
        self.file_tree.tag_configure("running", foreground=BLUE)

        dbc_head = ttk.Frame(body)
        dbc_head.pack(fill="x", pady=(14, 6))
        ttk.Label(dbc_head, text="2   DBC & kênh CAN", style="Section.TLabel").pack(side="left")
        self._button(dbc_head, "Thêm DBC…", self._add_dbc).pack(side="right")
        channel = self._register(ttk.Combobox(dbc_head, textvariable=self.dbc_channel, width=10, values=["Tất cả"] + [f"CAN {n}" for n in range(1, 33)], state="readonly"), "readonly")
        channel.pack(side="right", padx=6)
        ttk.Label(dbc_head, text="Áp dụng:", style="Muted.TLabel").pack(side="right")
        self._button(dbc_head, "Xóa đã chọn", self._remove_dbc).pack(side="right", padx=(0, 10))
        self.dbc_tree = self._tree(body, ("file", "channel"), ("Đường dẫn DBC", "Kênh"), (770, 120), 3)
        ttk.Label(body, text="Kênh đánh số từ 1. Nếu nhiều bus dùng cùng CAN ID, gán DBC riêng cho từng kênh.", style="Muted.TLabel").pack(anchor="w", pady=(5, 10))

        ttk.Label(body, text="3   Thư mục & định dạng đầu ra", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        output_row = ttk.Frame(body)
        output_row.pack(fill="x")
        self._register(ttk.Entry(output_row, textvariable=self.output)).pack(side="left", fill="x", expand=True)
        self._button(output_row, "Chọn thư mục…", self._choose_output).pack(side="right", padx=(8, 0))
        format_row = ttk.Frame(body)
        format_row.pack(fill="x", pady=(8, 3))
        self._register(ttk.Checkbutton(format_row, text="MF4 · MDF 4.10", variable=self.mf4)).pack(side="left")
        self._register(ttk.Checkbutton(format_row, text="Excel · XLSX", variable=self.excel)).pack(side="left", padx=(24, 0))
        ttk.Label(body, text="Giữ nguyên thời gian lấy mẫu. XLSX ghi nhiều sheet khi cần; xuất Excel có thể chiếm phần lớn thời gian.", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))

        tuning = ttk.LabelFrame(body, text="Tốc độ & bộ nhớ", padding=(12, 7, 12, 8))
        tuning.pack(fill="x")
        ttk.Label(tuning, text="Khối xử lý (MB):").pack(side="left")
        self._register(ttk.Combobox(tuning, textvariable=self.chunk, values=["16", "32", "64", "128"], width=6, state="readonly"), "readonly").pack(side="left", padx=(7, 22))
        ttk.Label(tuning, text="Nén MF4:").pack(side="left")
        self._register(ttk.Combobox(tuning, textvariable=self.compression, values=["0 — Nhanh nhất", "1 — Nén dung lượng"], width=21, state="readonly"), "readonly").pack(side="left", padx=(7, 0))
        ttk.Label(body, text="Mặc định ưu tiên tốc độ. Xử lý từng file giúp ổn định RAM với log lớn; nên lưu đầu ra trên SSD.", style="Muted.TLabel").pack(anchor="w", pady=(5, 13))

        action_row = ttk.Frame(body)
        action_row.pack(fill="x", pady=(0, 10))
        self.start_button = ttk.Button(action_row, text="Bắt đầu chuyển đổi", style="Primary.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.cancel_button = ttk.Button(action_row, text="Dừng", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)
        ttk.Button(action_row, text="Mở thư mục kết quả", command=self._open_output).pack(side="right")

        self.progress = ttk.Progressbar(body, maximum=100, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 7))
        ttk.Label(body, textvariable=self.status, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        ttk.Label(body, textvariable=self.detail, style="Muted.TLabel", wraplength=930).pack(anchor="w", pady=(3, 7))
        log_frame = ttk.Frame(body)
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(log_frame, height=4, wrap="word", relief="flat", borderwidth=0, background="#e9eef5", foreground=INK, font=("Consolas", 9), padx=9, pady=6, state="disabled")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _add_blf(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.root, title="Chọn file BLF", filetypes=[("Vector Binary Logging", "*.blf"), ("Tất cả", "*.*")])
        existing = {str(path).casefold() for path in self.files.values()}
        for name in paths:
            path = Path(name)
            if str(path).casefold() in existing:
                continue
            try:
                size = _size_text(path.stat().st_size)
            except OSError as exc:
                messagebox.showerror("Không đọc được file", str(exc), parent=self.root)
                continue
            item = self.file_tree.insert("", "end", values=(path.name, size, "Sẵn sàng"))
            self.files[item] = path
            existing.add(str(path).casefold())
            if not self.output.get():
                self.output.set(str(path.parent / "converted"))
        self.detail.set(f"{len(self.files)} file BLF đã chọn. Xử lý cục bộ trên máy.")

    def _remove_blf(self) -> None:
        for item in self.file_tree.selection():
            self.file_tree.delete(item)
            self.files.pop(item, None)

    def _add_dbc(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.root, title="Chọn DBC", filetypes=[("CAN database", "*.dbc"), ("Tất cả", "*.*")])
        label = self.dbc_channel.get()
        channel = None if label == "Tất cả" else int(label.split()[-1])
        existing = {(str(path).casefold(), bus) for path, bus in self.bindings.values()}
        for name in paths:
            path = Path(name)
            if (str(path).casefold(), channel) in existing:
                continue
            item = self.dbc_tree.insert("", "end", values=(str(path), label))
            self.bindings[item] = (path, channel)
            existing.add((str(path).casefold(), channel))

    def _remove_dbc(self) -> None:
        for item in self.dbc_tree.selection():
            self.dbc_tree.delete(item)
            self.bindings.pop(item, None)

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(parent=self.root, title="Chọn thư mục kết quả")
        if folder:
            self.output.set(folder)

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for widget, state in self._controls:
            widget.configure(state="disabled" if busy else state)
        self.start_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")

    def _start(self) -> None:
        if self.busy:
            return
        if not self.files:
            messagebox.showinfo("Chưa có file BLF", "Hãy thêm ít nhất một file BLF.", parent=self.root)
            return
        if not self.bindings:
            messagebox.showinfo("Chưa có DBC", "Hãy thêm DBC để giải mã tín hiệu.", parent=self.root)
            return
        if not self.output.get().strip():
            messagebox.showinfo("Chưa có thư mục kết quả", "Hãy chọn thư mục lưu kết quả.", parent=self.root)
            return
        if not (self.mf4.get() or self.excel.get()):
            messagebox.showinfo("Chưa chọn định dạng", "Chọn MF4, Excel hoặc cả hai.", parent=self.root)
            return
        inputs = list(self.files.items())
        settings = {
            "chunk_mb": int(self.chunk.get()),
            "write_mf4": self.mf4.get(),
            "write_excel": self.excel.get(),
            "compression": int(self.compression.get().split()[0]),
        }
        bindings = list(self.bindings.values())
        output = Path(self.output.get().strip()).expanduser()
        self.cancel.clear()
        self.progress["value"] = 0
        self.status.set("Đang chuẩn bị chuyển đổi…")
        self.detail.set("Bạn có thể dừng sau khi khối dữ liệu hiện tại xử lý xong.")
        for item, _ in inputs:
            values = list(self.file_tree.item(item, "values"))
            values[-1] = "Đang chờ"
            self.file_tree.item(item, values=values, tags=())
        self._set_busy(True)
        self.worker = threading.Thread(target=self._convert_batch, args=(inputs, bindings, output, settings), name="blf-converter", daemon=True)
        self.worker.start()

    def _convert_batch(self, inputs, bindings, output: Path, settings: dict) -> None:
        succeeded = failed = 0
        started = time.perf_counter()
        try:
            from .decoder import DBCBinding
            from .pipeline import ConversionOptions, convert_file

            dbc_bindings = [DBCBinding(path=path, channel=channel) for path, channel in bindings]
            options = ConversionOptions(**settings)
            for index, (item, source) in enumerate(inputs):
                if self.cancel.is_set():
                    break
                self.events.put(("begin", item, source.name, index, len(inputs)))

                def progress(*args, **kwargs):
                    self.events.put(("progress", index, len(inputs), args, kwargs))

                try:
                    result = convert_file(source, output, dbc_bindings, options, progress=progress, cancel=self.cancel)
                    succeeded += 1
                    self.events.put(("done", item, source.name, index, len(inputs), result))
                except Exception as exc:
                    if self.cancel.is_set():
                        self.events.put(("cancelled", item, source.name))
                        break
                    failed += 1
                    self.events.put(("error", item, source.name, str(exc)))
        except Exception as exc:
            failed += 1
            self.events.put(("fatal", str(exc)))
        finally:
            self.events.put(("finished", succeeded, failed, self.cancel.is_set(), time.perf_counter() - started))

    def _cancel(self) -> None:
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("Đang dừng…")
        self.detail.set("Đợi khối dữ liệu hiện tại hoàn tất để đóng file an toàn.")

    def _log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')}  {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_file_status(self, item: str, text: str, tag: str = "") -> None:
        values = list(self.file_tree.item(item, "values"))
        values[-1] = text
        self.file_tree.item(item, values=values, tags=(tag,) if tag else ())

    @staticmethod
    def _progress_info(args: tuple, kwargs: dict) -> tuple[float | None, str]:
        data: dict = dict(kwargs)
        if len(args) == 1 and isinstance(args[0], dict):
            data.update(args[0])
        elif len(args) == 1 and hasattr(args[0], "__dict__"):
            data.update(vars(args[0]))
        else:
            for value in args:
                if isinstance(value, str):
                    data.setdefault("message", value)
                elif isinstance(value, (int, float)):
                    data.setdefault("fraction", value)
        fraction = data.get("fraction", data.get("progress", data.get("percent")))
        if fraction is not None:
            try:
                fraction = float(fraction)
                if "percent" in data or fraction > 1:
                    fraction /= 100
                fraction = min(1.0, max(0.0, fraction))
            except (TypeError, ValueError):
                fraction = None
        message = str(data.get("message", data.get("detail", data.get("stage", "Đang xử lý…"))))
        return fraction, message

    def _drain(self) -> None:
        try:
            # A bounded drain keeps the event loop responsive even for a chatty worker.
            for _ in range(200):
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "begin":
                    _, item, name, index, total = event
                    self._set_file_status(item, "Đang chuyển đổi", "running")
                    self.file_tree.see(item)
                    self.status.set(f"[{index + 1}/{total}] {name}")
                    self._log(f"Bắt đầu: {name}")
                elif kind == "progress":
                    _, index, total, args, kwargs = event
                    fraction, message = self._progress_info(args, kwargs)
                    if fraction is not None:
                        self.progress["value"] = 100 * (index + fraction) / total
                    self.detail.set(message)
                elif kind == "done":
                    _, item, name, index, total, result = event
                    self._set_file_status(item, "Hoàn tất", "done")
                    self.progress["value"] = 100 * (index + 1) / total
                    elapsed = getattr(result, "elapsed_seconds", None)
                    suffix = f" ({elapsed:.1f} giây)" if isinstance(elapsed, (int, float)) else ""
                    self._log(f"Hoàn tất: {name}{suffix}")
                    warnings = getattr(result, "warnings", ()) or ()
                    for warning in warnings:
                        self._log(f"Lưu ý: {warning}")
                elif kind == "error":
                    _, item, name, error = event
                    self._set_file_status(item, "Lỗi", "error")
                    self._log(f"Lỗi {name}: {error}")
                elif kind == "cancelled":
                    _, item, name = event
                    self._set_file_status(item, "Đã dừng")
                    self._log(f"Đã dừng: {name}")
                elif kind == "fatal":
                    self._log(f"Không thể khởi chạy: {event[1]}")
                    messagebox.showerror("Không thể chuyển đổi", event[1], parent=self.root)
                elif kind == "finished":
                    _, succeeded, failed, cancelled, elapsed = event
                    self._set_busy(False)
                    label = "Đã dừng" if cancelled else "Đã xử lý xong"
                    self.status.set(f"{label} · {succeeded} thành công · {failed} lỗi · {elapsed:.1f} giây")
                    self.detail.set("Xem nhật ký bên dưới và báo cáo trong thư mục kết quả để kiểm tra tín hiệu không giải mã được.")
                    self._log(self.status.get())
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def _open_output(self) -> None:
        folder = Path(self.output.get()).expanduser()
        if not self.output.get() or not folder.is_dir():
            messagebox.showinfo("Thư mục chưa tồn tại", "Thư mục kết quả sẽ được tạo khi bắt đầu chuyển đổi.", parent=self.root)
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(folder.resolve()))
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder.resolve())])
        except OSError as exc:
            messagebox.showerror("Không mở được thư mục", str(exc), parent=self.root)

    def _close(self) -> None:
        if self.busy:
            self._cancel()
            self.detail.set("Đang dừng và đóng file. Cửa sổ sẽ tự đóng khi tác vụ kết thúc.")
            self.root.after(200, self._close_when_done)
        else:
            self.root.destroy()

    def _close_when_done(self) -> None:
        if self.busy:
            self.root.after(200, self._close_when_done)
        else:
            self.root.destroy()


def run() -> None:
    """Launch desktop interface, with Windows DPI awareness where available."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    ConverterApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
