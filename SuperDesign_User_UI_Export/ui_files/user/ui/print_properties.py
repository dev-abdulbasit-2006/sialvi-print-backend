"""
"Print Properties" popup.

Shown when the customer picks a print type that needs printer settings
(e.g. Glossy / Matte / Luster photo paper). The choices - paper size,
quality and paper type - are read from the real printer driver on the
Admin PC, saved with the order, and applied automatically when the
order is printed after payment is verified. Nothing is asked again on
the Admin PC.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QIcon
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QComboBox,
    QPushButton, QFrame,
)

from shared.resources import asset_path


class PrintPropertiesDialog(QDialog):
    def __init__(self, parent, printer_name: str, profile_name: str, options: dict, initial: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("Print Properties")
        self.setWindowIcon(QIcon(asset_path("emblem.png")))
        self.setModal(True)
        self.setMinimumWidth(520)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 22, 28, 24)
        lay.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(14)
        emblem = QLabel()
        pix = QPixmap(asset_path("emblem.png"))
        if not pix.isNull():
            emblem.setPixmap(pix.scaled(56, 56, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(emblem)
        col = QVBoxLayout()
        col.setSpacing(0)
        title = QLabel("Print Properties")
        title.setObjectName("PageTitle")
        sub = QLabel(f"How should “{profile_name}” be printed?")
        sub.setObjectName("Hint")
        col.addWidget(title)
        col.addWidget(sub)
        head.addLayout(col, 1)
        lay.addLayout(head)

        card = QFrame()
        card.setObjectName("SectionCard")
        form = QFormLayout(card)
        form.setContentsMargins(20, 18, 20, 18)
        form.setHorizontalSpacing(20)
        form.setVerticalSpacing(14)

        printer_label = QLabel(printer_name or "—")
        printer_label.setStyleSheet("font-weight: 700;")
        form.addRow("Printer:", printer_label)

        defaults = options.get("defaults", {})
        initial = initial or {}

        self.paper_combo = self._combo(options.get("paper_sizes", []),
                                       initial.get("paper_size") or defaults.get("paper_size"))
        form.addRow("Paper size:", self.paper_combo)

        self.quality_combo = self._combo(options.get("qualities", []),
                                         initial.get("quality") or defaults.get("quality"))
        form.addRow("Quality:", self.quality_combo)

        self.type_combo = self._combo(options.get("paper_types", []),
                                      initial.get("paper_type") or defaults.get("paper_type"))
        form.addRow("Paper type:", self.type_combo)
        lay.addWidget(card)

        note = QLabel("These settings are saved with your order and used automatically when it prints. "
                      "Make sure the paper in the printer matches your choice.")
        note.setObjectName("Hint")
        note.setWordWrap(True)
        lay.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save Properties")
        save.setProperty("class", "primary")
        save.setDefault(True)
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)

    @staticmethod
    def _combo(items: list[str], selected: str | None) -> QComboBox:
        combo = QComboBox()
        combo.setMinimumWidth(300)
        combo.setMaxVisibleItems(14)
        combo.addItems(items)
        if selected:
            i = combo.findText(selected)
            if i >= 0:
                combo.setCurrentIndex(i)
        return combo

    def values(self) -> dict:
        return {
            "paper_size": self.paper_combo.currentText(),
            "quality": self.quality_combo.currentText(),
            "paper_type": self.type_combo.currentText(),
        }
