"""Admin print queue monitor with working status filters and job actions."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QMessageBox, QScrollArea
from admin.ui.api_client import api_client, ApiClientError

STATUS = {
    "PRINTING": ("#3B82F6", "rgba(59,130,246,.12)", "●"),
    "QUEUED": ("#94A3B8", "rgba(148,163,184,.10)", "◷"),
    "FAILED": ("#EF4444", "rgba(239,68,68,.12)", "⚠"),
    "PRINT_ERROR": ("#EF4444", "rgba(239,68,68,.12)", "⚠"),
    "CANCELLED": ("#64748B", "rgba(100,116,139,.10)", "×"),
    "PRINTED": ("#22C55E", "rgba(34,197,94,.12)", "✓"),
}


def pill(status: str):
    fg, bg, dot = STATUS.get(status, ("#94A3B8", "rgba(148,163,184,.10)", "●"))
    label = {"PRINTED":"completed", "PRINT_ERROR":"failed"}.get(status, status.lower().replace("_", " "))
    w = QLabel(f"{dot}  {label}")
    w.setStyleSheet(
        f"color:{fg};background:{bg};border-radius:9px;padding:4px 9px;"
        "font-size:11px;font-weight:600;"
    )
    return w


class QueueScreen(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title_col = QVBoxLayout()
        title = QLabel("Print Queue")
        title.setObjectName("PageTitle")
        title_col.addWidget(title)
        sub = QLabel("Monitor every print job, its destination printer and result")
        sub.setObjectName("Hint")
        title_col.addWidget(sub)
        header.addLayout(title_col)
        header.addStretch()
        self.summary = QLabel("")
        self.summary.setObjectName("Hint")
        header.addWidget(self.summary)
        self.worker = QLabel("")
        header.addWidget(self.worker)
        refresh = QPushButton("Refresh")
        refresh.setProperty("class", "primary")
        refresh.clicked.connect(self.refresh)
        header.addWidget(refresh)
        layout.addLayout(header)

        tabs = QHBoxLayout()
        tabs.setSpacing(3)
        self.buttons = []
        for text, value in (("All", "all"), ("Printing", "printing"), ("Queued", "queued"),
                            ("Failed", "failed"), ("Completed", "completed")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(value == "all")
            b.setProperty("class", "primary" if value == "all" else "ghost")
            b.clicked.connect(lambda _, v=value: self.set_filter(v))
            tabs.addWidget(b)
            self.buttons.append((value, b))
        tabs.addStretch()
        layout.addLayout(tabs)

        self.error_banner = QLabel("")
        self.error_banner.setObjectName("StatusBannerError")
        self.error_banner.setVisible(False)
        self.error_banner.setWordWrap(True)
        layout.addWidget(self.error_banner)

        self.list_scroll = QScrollArea()
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QFrame.NoFrame)
        self.list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_scroll.viewport().setStyleSheet("background:#171717;border:none;")
        self.list_container = QWidget()
        self.list_container.setStyleSheet("background:#171717;")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        self.list_scroll.setWidget(self.list_container)
        layout.addWidget(self.list_scroll, 1)
        self._filter = "all"

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(3000)
        self.refresh()

    def set_filter(self, value):
        self._filter = value
        for current, b in self.buttons:
            b.setProperty("class", "primary" if current == value else "ghost")
            b.style().unpolish(b)
            b.style().polish(b)
        self.refresh()

    def clear(self):
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def job_card(self, job):
        card = QFrame()
        card.setObjectName("SectionCard")
        card.setMinimumHeight(112)
        lay = QHBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(14)

        status = str(job.get("status", "")).upper()
        fg, bg, icon = STATUS.get(status, ("#94A3B8", "rgba(148,163,184,.10)", "●"))
        icon_box = QLabel(icon)
        icon_box.setAlignment(Qt.AlignCenter)
        icon_box.setFixedSize(42, 42)
        icon_box.setStyleSheet(f"background:{bg};color:{fg};border-radius:11px;font-size:17px;")
        lay.addWidget(icon_box, alignment=Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(5)
        body.setContentsMargins(0, 2, 0, 2)
        top = QHBoxLayout()
        jid = QLabel(f"JOB-{str(job.get('id',''))[:8].upper()}")
        jid.setStyleSheet("font-family:Consolas;color:#94A3B8;font-size:11px;font-weight:700;")
        top.addWidget(jid)
        arrow = QLabel("→")
        arrow.setStyleSheet("color:#4B5563;")
        top.addWidget(arrow)
        oid = QLabel(f"ORD-{str(job.get('order_id',''))[:8].upper()}")
        oid.setStyleSheet("font-family:Consolas;color:#64748B;font-size:11px;")
        top.addWidget(oid)
        top.addWidget(pill(status))
        top.addStretch()
        body.addLayout(top)

        printer = QLabel(job.get("printer_name") or "Printer unavailable")
        printer.setStyleSheet("font-size:13px;color:#E2E8F0;font-weight:700;")
        body.addWidget(printer)

        meta = QHBoxLayout()
        meta.setSpacing(18)
        fields = [
            ("Profile", job.get("printing_type") or "—"),
            ("Pages", str(job.get("total_pages") or job.get("page_count") or "—")),
            ("Copies", str(job.get("copies") or "—")),
            ("Station", job.get("station_id") or "—"),
            ("Computer", job.get("computer_name") or "—"),
            ("File", job.get("original_filename") or "—"),
        ]
        for label, value in fields:
            w = QLabel(f"{label}: <span style='color:#94A3B8'>{value}</span>")
            w.setTextFormat(Qt.RichText)
            w.setStyleSheet("font-size:11px;color:#64748B;background:transparent;")
            w.setMinimumHeight(20)
            w.setToolTip(str(value))
            meta.addWidget(w)
        meta.addStretch()
        body.addLayout(meta)

        error = job.get("error_message")
        if error:
            err = QLabel(f"Error: {error}")
            err.setWordWrap(True)
            err.setStyleSheet("color:#FCA5A5;background:rgba(239,68,68,.07);border-radius:7px;padding:6px;font-size:11px;")
            body.addWidget(err)
        lay.addLayout(body, 1)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        if status == "QUEUED":
            cancel = QPushButton("✕ Cancel")
            cancel.setProperty("class", "danger")
            cancel.clicked.connect(lambda _, jid=job["id"]: self.cancel_job(jid))
            actions.addWidget(cancel)
        elif status in ("FAILED", "PRINT_ERROR"):
            retry = QPushButton("↻ Retry")
            retry.setProperty("class", "online")
            retry.clicked.connect(lambda _, jid=job["id"]: self.retry_job(jid))
            actions.addWidget(retry)
        else:
            actions.addStretch()
        lay.addLayout(actions)
        return card

    def refresh(self):
        try:
            jobs = api_client.get_queue("all") or []
            health = api_client.get_queue_status() or {}
        except ApiClientError as e:
            self.error_banner.setText("⚠ " + e.message)
            self.error_banner.setVisible(True)
            return
        self.error_banner.setVisible(False)

        counts = health.get("counts", {})
        self.worker.setText("● Worker Running" if health.get("worker_running") else "● Worker Stopped")
        self.worker.setStyleSheet(
            "color:#22C55E;font-weight:600;" if health.get("worker_running")
            else "color:#EF4444;font-weight:600;"
        )
        self.summary.setText(
            f"Printing: {counts.get('PRINTING',0)}  ·  Queued: {counts.get('QUEUED',0)}  ·  "
            f"Failed: {counts.get('FAILED',0)+counts.get('PRINT_ERROR',0)}"
        )

        wanted = {
            "all": lambda st: True,
            "printing": lambda st: st == "PRINTING",
            "queued": lambda st: st == "QUEUED",
            "failed": lambda st: st in ("FAILED", "PRINT_ERROR"),
            "completed": lambda st: st == "PRINTED",
        }[self._filter]
        jobs = [j for j in jobs if wanted(str(j.get("status","")).upper())]

        self.clear()
        for job in jobs:
            self.list_layout.addWidget(self.job_card(job))
        if not jobs:
            empty = QFrame()
            empty.setObjectName("SectionCard")
            ev = QVBoxLayout(empty)
            ev.setContentsMargins(20, 36, 20, 36)
            text = QLabel("No jobs in this category")
            text.setObjectName("Hint")
            text.setAlignment(Qt.AlignCenter)
            ev.addWidget(text)
            self.list_layout.addWidget(empty)
        self.list_layout.addStretch()

    def cancel_job(self, job_id):
        if QMessageBox.question(self, "Cancel print job", "Cancel this queued print job?") != QMessageBox.Yes:
            return
        try:
            api_client.cancel_queue_job(job_id)
        except ApiClientError as e:
            QMessageBox.critical(self, "Cancel failed", e.message)
            return
        self.refresh()

    def retry_job(self, job_id):
        if QMessageBox.question(self, "Retry print job", "Put this failed job back into the working queue?") != QMessageBox.Yes:
            return
        try:
            api_client.retry_queue_job(job_id)
        except ApiClientError as e:
            QMessageBox.critical(self, "Retry failed", e.message)
            return
        self.refresh()
