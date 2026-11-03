"""Sialvi Print Owner License Manager - main window (tabs: Overview, Licenses, Installations,
IP Restrictions, Audit History). The server enforces everything; this is only the owner's console."""
import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QDialog, QFileDialog, QPlainTextEdit, QComboBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPushButton, QTableWidget, QTabWidget, QVBoxLayout, QWidget, QFrame)

from owner.api import ApiError
from owner.ui.dialogs import (ConflictDialog, CreateLicenseDialog, KeyDialog, LimitDialog, RestrictionDialog,
                              TableDialog, fill_table, ts)
from shared.resources import asset_path
from licensing.embed_config import fingerprint, write_embedded

logger = logging.getLogger("owner_ui")

# (code, label) shown in the Remote Commands tab. The server applies the change at once; PCs are told at their next poll.
COMMAND_CHOICES = [
    ("SUSPEND_LICENSE", "Suspend whole license"),
    ("REACTIVATE_LICENSE", "Reactivate whole license"),
    ("REVOKE_LICENSE", "Revoke license (permanent)"),
    ("DISABLE_DEVICE", "Disable one PC (lock)"),
    ("REENABLE_DEVICE", "Re-enable one PC (unlock)"),
    ("REVOKE_DEVICE", "Remove one PC (frees its slot)"),
    ("FORCE_REVALIDATE", "Ask PCs to re-check now"),
]
DEVICE_COMMANDS = {"DISABLE_DEVICE", "REENABLE_DEVICE", "REVOKE_DEVICE"}
DELIVERY_LABELS = {"waiting": "Waiting for PCs", "complete": "Done", "problem": "Needs attention",
                   "expired": "Expired (not delivered)", "no_pcs": "No PCs"}


def btn(text, cls=None, slot=None):
    b = QPushButton(text)
    if cls:
        b.setProperty("class", cls)
    if slot:
        b.clicked.connect(slot)
    return b


class MainWindow(QMainWindow):
    def __init__(self, api):
        super().__init__()
        self.api = api
        self.setWindowTitle("Sialvi Print — Owner License Manager")
        self.setWindowIcon(QIcon(asset_path("emblem.png")))
        self.resize(1180, 740)
        self.setMinimumSize(1000, 620)
        self.lic_rows, self.inst_rows, self.res_rows, self.cmd_rows = [], [], [], []

        root = QWidget(); self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(18, 14, 18, 16); lay.setSpacing(12)
        top = QHBoxLayout()
        title = QLabel("Owner License Manager"); title.setObjectName("PageTitle")
        self.who = QLabel(f"Signed in as {api.username}  •  {api.base}"); self.who.setObjectName("Hint")
        top.addWidget(title); top.addStretch(); top.addWidget(self.who)
        top.addWidget(btn("Refresh", "ghost", self.refresh_all)); top.addWidget(btn("Sign out", "ghost", self.close))
        lay.addLayout(top)
        # Shown when the cloud server keeps its data on a temporary disk: licenses and PCs would vanish on restart.
        self.storage_banner = QLabel(""); self.storage_banner.setObjectName("StatusBannerError")
        self.storage_banner.setWordWrap(True); self.storage_banner.setVisible(False)
        lay.addWidget(self.storage_banner)
        self.tabs = QTabWidget(); lay.addWidget(self.tabs, 1)
        self._build_overview(); self._build_licenses(); self._build_installations(); self._build_restrictions(); self._build_audit(); self._build_commands(); self._build_app_builds()
        for i in range(self.tabs.count()):
            self.tabs.widget(i).layout().setContentsMargins(14, 14, 14, 14)
            self.tabs.widget(i).layout().setSpacing(10)
        self.tabs.currentChanged.connect(lambda _i: self.refresh_current())
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh_current); self.timer.start(60_000)
        self.refresh_all()

    # ------------------------------------------------------------ helpers
    def call(self, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except ApiError as e:
            if e.status == 401:
                QMessageBox.warning(self, "Session ended", "Your owner session ended. Please sign in again.")
                self.close()
            else:
                QMessageBox.warning(self, "Request refused", e.message)
            return None
        except Exception as e:          # never fail silently (a windowed .exe has no console)
            logger.exception("Unexpected error")
            QMessageBox.critical(self, "Unexpected error", f"{type(e).__name__}: {e}\n\nDetails were saved in the owner.log file.")
            return None

    def confirm(self, title, text):
        return QMessageBox.question(self, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def ask_reason(self, title, prompt="Reason (optional, saved in the audit log):"):
        text, ok = QInputDialog.getText(self, title, prompt)
        return (text, ok)

    @staticmethod
    def bind_selection(table, buttons):
        """Action buttons are greyed out until a row is selected."""
        def update():
            on = table.currentRow() >= 0 and bool(table.selectedItems())
            for b in buttons:
                b.setEnabled(on)
        table.itemSelectionChanged.connect(update)
        update()

    @staticmethod
    def selected_row(table, rows):
        r = table.currentRow()
        if r < 0:
            return None
        item = table.item(r, 0)
        if item is None:
            return None
        key = item.data(Qt.UserRole)
        if key is None:
            key = item.text()
        return next((x for x in rows if str(x["_key"]) == str(key)), None)

    def check_storage(self):
        """Ask the (public) health endpoint where the server keeps its data and warn if it is not persistent."""
        try:
            h = self.api.health()
        except Exception:                      # noqa: BLE001  (an unreachable server is reported by the normal refresh)
            return
        storage = str((h or {}).get("storage", "")).lower()
        persistent = bool((h or {}).get("persistent"))
        self.who.setText(f"Signed in as {self.api.username}  •  {self.api.base}  •  data: {storage or 'unknown'}")
        self.storage_banner.setVisible(not persistent)
        if not persistent:
            self.storage_banner.setText(
                "⚠ The licensing server keeps its data on a TEMPORARY disk. Licenses, PCs and your owner session will be LOST "
                "whenever the server restarts or goes to sleep, and customers will see \"installation not recognized\". "
                "Set DATABASE_URL on the server (see documentation/CLOUD_LICENSING_RENDER.md).")

    def refresh_all(self):
        self.check_storage()
        self.refresh_overview(); self.refresh_licenses(); self.refresh_installations(); self.refresh_restrictions(); self.refresh_audit(); self.refresh_commands()

    def refresh_current(self):
        ([self.refresh_overview, self.refresh_licenses, self.refresh_installations, self.refresh_restrictions, self.refresh_audit, self.refresh_commands, lambda: None][self.tabs.currentIndex()])()

    # ------------------------------------------------------------ overview
    def _build_overview(self):
        w = QWidget(); g = QGridLayout(w); self.cards = {}
        spec = [("total_licenses", "Total licenses"), ("active_licenses", "Active licenses"),
                ("suspended_licenses", "Suspended"), ("revoked_licenses", "Revoked"), ("expired_licenses", "Expired"),
                ("registered_installations", "Registered PCs"), ("locked_installations", "Locked PCs"),
                ("recently_seen_installations", "Recently seen PCs")]
        for i, (k, label) in enumerate(spec):
            f = QFrame(); f.setObjectName("StatCard")
            v = QVBoxLayout(f); v.setContentsMargins(18, 14, 18, 14)
            n = QLabel("—"); n.setObjectName("StatNumber")
            t = QLabel(label); t.setObjectName("StatLabel"); v.addWidget(n); v.addWidget(t)
            self.cards[k] = n; g.addWidget(f, i // 4, i % 4)
        g.setHorizontalSpacing(14); g.setVerticalSpacing(14)
        self.policy = QLabel(); self.policy.setWordWrap(True); self.policy.setObjectName("Hint")
        g.addWidget(self.policy, 2, 0, 1, 4); g.setRowStretch(3, 1)
        self.tabs.addTab(w, "Overview")

    def refresh_overview(self):
        d = self.call(self.api.overview)
        if d is None:
            return
        for k, lab in self.cards.items():
            lab.setText(str(d.get(k, "—")))
        p = self.api.policy
        self.policy.setText(f"Policy: PCs check in every {p.get('checkin_interval_minutes')} min; a PC keeps working offline for "
                            f"{p.get('offline_grace_hours')} h after its last successful check-in; shown as “Not recently seen” after "
                            f"{p.get('not_recent_minutes')} min. “Recently seen” is NOT proof a PC is online right now.")

    # ------------------------------------------------------------ licenses
    def _build_licenses(self):
        w = QWidget(); lay = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText("Search name, notes, ID or key ending…")
        self.search.returnPressed.connect(self.refresh_licenses)
        bar.addWidget(self.search, 1); bar.addWidget(btn("Search", "ghost", self.refresh_licenses))
        bar.addWidget(btn("＋ Create New License", "primary", self.create_license))
        lay.addLayout(bar)
        hint = QLabel("Select a license in the list, then use the buttons below it."); hint.setObjectName("Hint"); lay.addWidget(hint)
        self.lic_table = QTableWidget(); lay.addWidget(self.lic_table, 1)
        self.lic_empty = QLabel("No licenses yet.\nClick  “＋ Create New License”  to make your first serial key.")
        self.lic_empty.setObjectName("Empty"); self.lic_empty.setAlignment(Qt.AlignCenter); lay.addWidget(self.lic_empty)
        row = QHBoxLayout(); self.lic_actions = []
        for text, cls, slot in [("View PCs", None, self.view_pcs), ("Edit PC Limit", None, self.edit_limit),
                                ("Edit Details", None, self.edit_details), ("Suspend", None, lambda: self.lic_action("suspend")),
                                ("Reactivate", None, lambda: self.lic_action("reactivate")),
                                ("Revoke", "danger", lambda: self.lic_action("revoke")), ("History", "ghost", self.lic_history)]:
            b = btn(text, cls, slot); self.lic_actions.append(b); row.addWidget(b)
        row.addStretch(); lay.addLayout(row)
        self.bind_selection(self.lic_table, self.lic_actions)
        self.tabs.addTab(w, "Licenses")

    def refresh_licenses(self):
        data = self.call(self.api.licenses, self.search.text().strip())
        if data is None:
            return
        self.lic_rows = [dict(d, _key=d["display_id"]) for d in data]
        self.lic_empty.setVisible(not self.lic_rows); self.lic_table.setVisible(bool(self.lic_rows))
        fill_table(self.lic_table, ["ID", "Customer / label", "Status", "PCs used / limit", "Key (hint)", "Created (UTC)", "Expires (UTC)", "Notes"],
                   [[d["display_id"], d["label"], d["effective_status"].capitalize(), d["usage_text"], d["key_display"],
                     ts(d["created_at"]), ts(d["expires_at"]), d["notes"][:60]] for d in self.lic_rows],
                   keys=[d["_key"] for d in self.lic_rows])

    def cur_lic(self):
        d = self.selected_row(self.lic_table, self.lic_rows)
        if not d:
            QMessageBox.information(self, "Select a license", "Please select a license in the table first.")
        return d

    def create_license(self):
        dlg = CreateLicenseDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        res = self.call(self.api.create_license, **dlg.values())
        if not res or "serial_key" not in res:
            return                      # call() already told the owner what went wrong
        self.refresh_licenses(); self.refresh_overview()
        KeyDialog(res["license"]["label"], res["serial_key"], res["license"]["usage_text"], self).exec()

    def edit_limit(self):
        d = self.cur_lic()
        if not d:
            return
        dlg = LimitDialog(d["label"], d["max_installations"], d["registered_pcs"], self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        n, reason, ids = dlg.limit.value(), dlg.reason.text(), []
        while True:
            try:
                self.api.set_limit(d["id"], n, ids, reason)
                break
            except ApiError as e:
                if e.code == "LIMIT_CONFLICT":
                    cd = ConflictDialog(e.extra, self)
                    if cd.exec() != QDialog.DialogCode.Accepted:
                        return
                    ids = cd.selected()
                    continue
                if e.status == 401:
                    self.call(lambda: (_ for _ in ()).throw(e))
                else:
                    QMessageBox.warning(self, "Request refused", e.message)
                return
        self.refresh_all()

    def edit_details(self):
        d = self.cur_lic()
        if not d:
            return
        name, ok = QInputDialog.getText(self, "Edit details", "Customer / license name:", text=d["label"])
        if not ok:
            return
        notes, ok = QInputDialog.getText(self, "Edit details", "Notes:", text=d["notes"])
        if not ok:
            return
        exp, ok = QInputDialog.getText(self, "Edit details", "Expiration date YYYY-MM-DD (empty = never):", text=(d["expires_at"] or "")[:10])
        if ok and self.call(self.api.edit_license, d["id"], label=name, notes=notes, expires_at=exp.strip() or None):
            self.refresh_licenses()

    def lic_action(self, action):
        d = self.cur_lic()
        if not d:
            return
        effect = {"suspend": "New activations and renewals for ALL PCs of this license will be refused.",
                  "reactivate": "The license will accept activations and renewals again.",
                  "revoke": "PERMANENT. This key can never authorize PCs again (create a new license instead)."}[action]
        if not self.confirm(f"{action.capitalize()} license", f"{d['display_id']} — {d['label']}\n\n{effect}\n\nContinue?"):
            return
        reason, ok = self.ask_reason(f"{action.capitalize()} license")
        if ok and self.call(self.api.license_action, d["id"], action, reason):
            self.refresh_all()

    def lic_history(self):
        d = self.cur_lic()
        if d:
            self.show_audit_dialog(f"History — {d['label']}", self.call(self.api.audit, d["id"]))

    def view_pcs(self):
        d = self.cur_lic()
        if d:
            self.inst_filter.setCurrentIndex(max(0, self.inst_filter.findData(d["id"])))
            self.tabs.setCurrentIndex(2)

    # ------------------------------------------------------------ installations
    def _build_installations(self):
        w = QWidget(); lay = QVBoxLayout(w)
        bar = QHBoxLayout(); bar.addWidget(QLabel("License:"))
        self.inst_filter = QComboBox(); self.inst_filter.addItem("All licenses", None)
        self.inst_filter.currentIndexChanged.connect(self.refresh_installations)
        bar.addWidget(self.inst_filter, 1); lay.addLayout(bar)
        self.inst_table = QTableWidget(); lay.addWidget(self.inst_table)
        row = QHBoxLayout(); self.inst_actions = []
        for text, cls, slot in [("Lock Installation", "danger", self.lock), ("Unlock Installation", None, self.unlock),
                                ("Deactivate", None, self.deactivate), ("IP History", "ghost", self.ip_history),
                                ("Events", "ghost", self.events)]:
            b = btn(text, cls, slot); self.inst_actions.append(b); row.addWidget(b)
        row.addStretch(); lay.addLayout(row)
        self.bind_selection(self.inst_table, self.inst_actions)
        self.tabs.addTab(w, "Installations")

    def refresh_installations(self):
        cur = self.inst_filter.currentData()
        self.inst_filter.blockSignals(True)
        self.inst_filter.clear(); self.inst_filter.addItem("All licenses", None)
        for l in self.lic_rows:
            self.inst_filter.addItem(f"{l['display_id']}  {l['label']}", l["id"])
        self.inst_filter.setCurrentIndex(max(0, self.inst_filter.findData(cur))); self.inst_filter.blockSignals(False)
        data = self.call(self.api.installations, self.inst_filter.currentData())
        if data is None:
            return
        self.inst_rows = [dict(d, _key=d["id"]) for d in data]
        fill_table(self.inst_table, ["Device", "App / version", "License", "Status", "Last public IP", "Last check-in (UTC)",
                                     "Operating system", "First activation (UTC)", "Installation ID"],
                   [[d["device_name"] or "—", d["app_version"] or "—", d["license_label"], d["display_status"], d["last_ip"] or "—",
                     f"{ts(d['last_checkin_at'])}  ({d['last_seen']})", d["os_info"] or "—", ts(d["activated_at"]), d["id"]]
                    for d in self.inst_rows], keys=[d["_key"] for d in self.inst_rows])

    def cur_inst(self):
        d = self.selected_row(self.inst_table, self.inst_rows)
        if not d:
            QMessageBox.information(self, "Select a PC", "Please select an installation first.")
        return d

    def lock(self):
        d = self.cur_inst()
        if not d:
            return
        if not self.confirm("Lock installation", f"Lock “{d['device_name'] or d['id']}”?\n\nOnly this PC is locked. It is refused at its next "
                            "check-in; if it is offline it keeps working until its offline grace period ends."):
            return
        reason, ok = self.ask_reason("Lock installation")
        if ok and self.call(self.api.inst_action, d["id"], "lock", reason):
            self.refresh_all()

    def unlock(self):
        d = self.cur_inst()
        if d and self.confirm("Unlock installation", f"Unlock “{d['device_name'] or d['id']}”?\n(The license itself must also be valid.)"):
            reason, ok = self.ask_reason("Unlock installation")
            if ok:
                res = self.call(self.api.inst_action, d["id"], "unlock", reason)
                if res and res.get("warning"):
                    QMessageBox.information(self, "Unlocked with warning", res["warning"])
                self.refresh_all()

    def deactivate(self):
        d = self.cur_inst()
        if d and self.confirm("Deactivate installation", f"Deactivate “{d['device_name'] or d['id']}”?\nIt frees a PC slot; history is kept. "
                              "The PC must be activated again to work."):
            reason, ok = self.ask_reason("Deactivate installation")
            if ok and self.call(self.api.inst_action, d["id"], "deactivate", reason):
                self.refresh_all()

    def ip_history(self):
        d = self.cur_inst()
        if d:
            rows = self.call(self.api.ip_history, d["id"]) or []
            TableDialog(f"IP history — {d['device_name'] or d['id']}", ["Time (UTC)", "Public IP", "Event", "Result", "App version"],
                        [[ts(r["ts"]), r["ip"], r["event"], r["result"], r["app_version"]] for r in rows], self).exec()

    def events(self):
        d = self.cur_inst()
        if d:
            self.show_audit_dialog(f"Events — {d['device_name'] or d['id']}", self.call(self.api.inst_events, d["id"]))

    def show_audit_dialog(self, title, events):
        TableDialog(title, ["Time (UTC)", "Who", "Action", "Target", "Reason", "Before → After"],
                    [[ts(e["ts"]), e["actor"], e["action"], f"{e['target_type']} {e['target_id']}", e["reason"],
                      f"{e['before_json'] or ''} → {e['after_json'] or ''}"] for e in (events or [])], self).exec()

    # ------------------------------------------------------------ IP restrictions
    def _build_restrictions(self):
        w = QWidget(); lay = QVBoxLayout(w)
        bar = QHBoxLayout(); bar.addStretch(); bar.addWidget(btn("＋ Add IP Restriction", "primary", self.add_restriction)); lay.addLayout(bar)
        self.res_table = QTableWidget(); lay.addWidget(self.res_table)
        row = QHBoxLayout()
        self.res_actions = [btn("Disable", None, lambda: self.res_state("disable")), btn("Enable", None, lambda: self.res_state("enable"))]
        for b in self.res_actions:
            row.addWidget(b)
        row.addStretch(); lay.addLayout(row)
        self.bind_selection(self.res_table, self.res_actions)
        self.tabs.addTab(w, "IP Restrictions")

    def refresh_restrictions(self):
        data = self.call(self.api.restrictions)
        if data is None:
            return
        names = {"block_activation": "Block new activations", "block_authorization": "Block all authorization"}
        self.res_rows = [dict(d, _key=d["id"]) for d in data]
        fill_table(self.res_table, ["#", "IP / range", "What is blocked", "Applies to", "Reason", "State", "Created (UTC)", "By"],
                   [[d["id"], d["network"], names[d["scope"]], d["license_label"] or "ALL licenses", d["reason"],
                     "Active" if d["active"] else "Disabled", ts(d["created_at"]), d["created_by"]] for d in self.res_rows],
                   keys=[d["_key"] for d in self.res_rows])

    def add_restriction(self):
        dlg = RestrictionDialog(self.lic_rows, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        v = dlg.values()
        if v["license_id"] is None:
            if not self.confirm("Global IP restriction", "This applies to ALL licenses. Other customers who share this public IP "
                                "(same office, ISP or mobile carrier) may be blocked too.\n\nApply it globally?"):
                return
            v["confirm_global"] = True
        if self.call(self.api.add_restriction, **v):
            self.refresh_restrictions()

    def res_state(self, state):
        d = self.selected_row(self.res_table, self.res_rows)
        if not d:
            QMessageBox.information(self, "Select a restriction", "Please select a restriction first."); return
        if self.confirm(f"{state.capitalize()} restriction", f"{state.capitalize()} the restriction on {d['network']}?\n"
                        "This does not change any independent PC lock."):
            if self.call(self.api.restriction_state, d["id"], state):
                self.refresh_restrictions()

    # ------------------------------------------------------------ audit
    def _build_audit(self):
        w = QWidget(); lay = QVBoxLayout(w)
        self.audit_table = QTableWidget(); lay.addWidget(self.audit_table)
        self.tabs.addTab(w, "Audit History")

    def refresh_audit(self):
        ev = self.call(self.api.audit)
        if ev is None:
            return
        for e in ev:
            e["_key"] = e["id"]
        fill_table(self.audit_table, ["#", "Time (UTC)", "Who", "Action", "Target", "Reason", "Before", "After", "IP"],
                   [[e["id"], ts(e["ts"]), e["actor"], e["action"], f"{e['target_type']} {e['target_id']}", e["reason"],
                     e["before_json"] or "", e["after_json"] or "", e["ip"] or ""] for e in ev])

    # ------------------------------------------------------------ remote commands
    def _build_commands(self):
        w = QWidget(); lay = QVBoxLayout(w)
        intro = QLabel("Send a command to a shop's PCs. The change is applied on the licensing server immediately; every PC that is "
                       "online picks it up within about 30 seconds and reports back. A PC that is offline applies it when it "
                       "reconnects (it is not instantly reachable). Delivery status is shown below.")
        intro.setObjectName("Hint"); intro.setWordWrap(True); lay.addWidget(intro)
        form = QGridLayout()
        self.cmd_license = QComboBox(); self.cmd_license.currentIndexChanged.connect(self._fill_cmd_pcs)
        self.cmd_type = QComboBox()
        for code, text in COMMAND_CHOICES:
            self.cmd_type.addItem(text, code)
        self.cmd_type.currentIndexChanged.connect(self._cmd_type_changed)
        self.cmd_pc = QComboBox()
        self.cmd_reason = QLineEdit(); self.cmd_reason.setPlaceholderText("Required, e.g. Invoice payment pending (saved in the audit log)")
        form.addWidget(QLabel("License:"), 0, 0); form.addWidget(self.cmd_license, 0, 1)
        form.addWidget(QLabel("PC:"), 0, 2); form.addWidget(self.cmd_pc, 0, 3)
        form.addWidget(QLabel("Command:"), 1, 0); form.addWidget(self.cmd_type, 1, 1)
        form.addWidget(QLabel("Reason:"), 1, 2); form.addWidget(self.cmd_reason, 1, 3)
        form.addWidget(btn("Send Command", "primary", self.send_command), 1, 4)
        form.setColumnStretch(1, 1); form.setColumnStretch(3, 2)
        lay.addLayout(form)
        self.cmd_table = QTableWidget(); lay.addWidget(self.cmd_table)
        row = QHBoxLayout(); self.cmd_actions = [btn("Delivery Details", "ghost", self.command_details)]
        for b in self.cmd_actions:
            row.addWidget(b)
        row.addStretch(); lay.addLayout(row)
        self.bind_selection(self.cmd_table, self.cmd_actions)
        self.tabs.addTab(w, "Remote Commands")
        self._cmd_type_changed()

    def _cmd_type_changed(self):
        code = self.cmd_type.currentData()
        self.cmd_pc.setEnabled(code in DEVICE_COMMANDS or code == "FORCE_REVALIDATE")

    def _fill_cmd_pcs(self):
        self.cmd_pc.clear(); self.cmd_pc.addItem("All PCs of this license", None)
        lid = self.cmd_license.currentData()
        if lid is None:
            return
        for d in self.call(self.api.installations, lid) or []:
            self.cmd_pc.addItem(f"{d['device_name'] or d['id']} — {d['display_status']}", d["id"])

    def refresh_commands(self):
        cur = self.cmd_license.currentData()
        self.cmd_license.blockSignals(True)
        self.cmd_license.clear()
        for l in self.lic_rows:
            self.cmd_license.addItem(f"{l['display_id']}  {l['label']}", l["id"])
        self.cmd_license.setCurrentIndex(max(0, self.cmd_license.findData(cur)))
        self.cmd_license.blockSignals(False)
        if self.cmd_pc.count() == 0 or self.cmd_license.currentData() != cur:
            self._fill_cmd_pcs()
        data = self.call(self.api.commands)
        if data is None:
            return
        self.cmd_rows = [dict(d, _key=d["id"]) for d in data]
        fill_table(self.cmd_table, ["Sent (UTC)", "License", "Command", "Target", "Delivery", "Applied", "Reason", "Sent by"],
                   [[ts(c["issued_at"]), c["license_label"], c["command_type"], c["installation_id"] or "Whole license",
                     DELIVERY_LABELS.get(c["summary"]["overall"], c["summary"]["overall"]),
                     f"{c['summary']['applied']} / {c['summary']['total']}", c["reason"], c["issued_by"]] for c in self.cmd_rows],
                   keys=[c["_key"] for c in self.cmd_rows])

    def send_command(self):
        lid, code = self.cmd_license.currentData(), self.cmd_type.currentData()
        if lid is None:
            QMessageBox.information(self, "Select a license", "Please choose the license first."); return
        iid = self.cmd_pc.currentData() if (code in DEVICE_COMMANDS or code == "FORCE_REVALIDATE") else None
        if code in DEVICE_COMMANDS and iid is None:
            QMessageBox.information(self, "Select a PC", "This command is for one PC. Please choose the PC."); return
        reason = self.cmd_reason.text().strip()
        if code != "FORCE_REVALIDATE" and len(reason) < 3:
            QMessageBox.information(self, "Reason required", "Please type a reason (it is saved in the audit log)."); return
        label = self.cmd_type.currentText()
        extra = "\n\nThis is PERMANENT: a revoked license cannot be reactivated." if code == "REVOKE_LICENSE" else ""
        if not self.confirm("Send command", f"{label}\nLicense: {self.cmd_license.currentText()}\n"
                            f"Target: {self.cmd_pc.currentText() if iid else 'all PCs of this license'}{extra}\n\nSend it now?"):
            return
        res = self.call(self.api.issue_command, lid, code, iid, reason)
        if res:
            self.cmd_reason.clear()
            if res.get("warning"):
                QMessageBox.information(self, "Sent with a warning", res["warning"])
            self.refresh_all()

    def command_details(self):
        d = self.selected_row(self.cmd_table, self.cmd_rows)
        if not d:
            QMessageBox.information(self, "Select a command", "Please select a command first."); return
        full = self.call(self.api.command_detail, d["id"])
        if full:
            names = {"pending": "Waiting", "delivered": "Delivered, no answer yet", "applied": "Applied", "rejected": "Not applied", "expired": "Expired"}
            TableDialog(f"Delivery — {full['command_type']}", ["PC", "Installation ID", "Status", "Delivered (UTC)", "Answered (UTC)", "Result"],
                        [[x["device_name"] or "—", x["installation_id"], names.get(x["status"], x["status"]), ts(x["delivered_at"]),
                          ts(x["acknowledged_at"]), x["result"]] for x in full["deliveries"]], self).exec()

    # ------------------------------------------------------------ app builds (links Admin/User to this owner tool)
    def _build_app_builds(self):
        w = QWidget(); lay = QVBoxLayout(w)
        info = QLabel(
            "<b>Connect the Admin and User apps to the CLOUD licensing server.</b><br>"
            "Every SialviPrint Admin and User app is built with your server address and public key inside it. "
            "Without a valid serial key from this Owner License Manager they show a license screen and do nothing else "
            "(no orders, no printing). Pending print jobs are never deleted - they wait.<br><br>"
            "1) Click <i>Fetch server key</i> and check the fingerprint.  2) Click <i>Save settings file</i> and save it as "
            "<code>shared/license_embedded.py</code> in the project.  3) Run <code>installer\\build_all.bat</code> "
            "(it refuses to build if this step was skipped).  4) Give customers the installers + a serial key. "
            "Each PC running the Admin or User app uses one PC slot of its key.")
        info.setWordWrap(True); lay.addWidget(info)
        self.pk_box = QPlainTextEdit(); self.pk_box.setReadOnly(True); self.pk_box.setFixedHeight(130); lay.addWidget(self.pk_box)
        self.pk_fp = QLabel(""); lay.addWidget(self.pk_fp)
        row = QHBoxLayout()
        row.addWidget(btn("Fetch server key", "primary", self.fetch_key)); row.addWidget(btn("Save settings file…", None, self.save_embedded))
        row.addStretch(); lay.addLayout(row); lay.addStretch()
        self.tabs.addTab(w, "App Builds")

    def fetch_key(self):
        pem = self.call(self.api.public_key)
        if pem:
            self.pk_box.setPlainText(pem)
            self.pk_fp.setText(f"Server address: <b>{self.api.base}</b><br>Public key fingerprint: <b>{fingerprint(pem)}</b>")

    def save_embedded(self):
        pem = self.pk_box.toPlainText().strip()
        if not pem:
            QMessageBox.information(self, "Fetch the key first", "Click “Fetch server key” first."); return
        path, _ = QFileDialog.getSaveFileName(self, "Save license_embedded.py", "license_embedded.py", "Python (*.py)")
        if not path:
            return
        try:
            write_embedded(self.api.base, pem, path)
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Cannot save", str(e)); return
        QMessageBox.information(self, "Saved", f"Saved to {path}.\nCopy it over <project>/shared/license_embedded.py, then build the apps.")

    def closeEvent(self, event):
        self.api.logout()
        event.accept()
