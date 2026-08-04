"""Reusable disclosure section for the PySide6 main window."""

from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CollapsibleSection(QWidget):
    """A keyboard-accessible header that shows or hides one content widget."""

    toggled = Signal(str, bool)
    HEADER_STYLE = """
        QToolButton {
            font-weight: 600;
            text-align: left;
            color: #35434d;
            padding: 7px 9px;
            border: 1px solid #cbd2d7;
            border-radius: 5px;
            background-color: #f3f5f6;
        }
        QToolButton:hover {
            background-color: #edf1f3;
            border-color: #aebbc4;
        }
        QToolButton:focus {
            border-color: #5f879f;
        }
        QToolButton[sectionState="expanded"] {
            color: #23465a;
            background-color: #e8f0f4;
            border-color: #9eb5c3;
        }
        QToolButton[sectionState="expanded"]:hover {
            background-color: #e1ebf0;
            border-color: #829fac;
        }
    """

    def __init__(
        self,
        key: str,
        title: str,
        content: QWidget,
        expanded: bool = True,
        fill_available: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.key = key
        self.content = content
        self.fill_available = fill_available

        if isinstance(content, QGroupBox):
            content.setTitle("")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)

        self.toggle_button = QToolButton(self)
        self.toggle_button.setObjectName(f"sectionToggle_{key}")
        self.toggle_button.setAccessibleName(f"{title} section")
        self.toggle_button.setText(title)
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self.toggle_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.toggle_button.setStyleSheet(self.HEADER_STYLE)
        self.toggle_button.toggled.connect(self._on_toggled)

        content.setObjectName(f"sectionContent_{key}")
        layout.addWidget(self.toggle_button)
        layout.addWidget(content)
        self.set_expanded(expanded, emit_signal=False)

    def is_expanded(self) -> bool:
        return self.toggle_button.isChecked()

    def set_expanded(self, expanded: bool, emit_signal: bool = True) -> None:
        self.toggle_button.blockSignals(True)
        self.toggle_button.setChecked(bool(expanded))
        self.toggle_button.blockSignals(False)
        self._apply_state(bool(expanded))
        if emit_signal:
            self.toggled.emit(self.key, bool(expanded))

    def _on_toggled(self, expanded: bool) -> None:
        self._apply_state(expanded)
        self.toggled.emit(self.key, expanded)

    def _apply_state(self, expanded: bool) -> None:
        self.content.setVisible(expanded)
        self.toggle_button.setProperty(
            "sectionState", "expanded" if expanded else "collapsed"
        )
        style = self.toggle_button.style()
        style.unpolish(self.toggle_button)
        style.polish(self.toggle_button)
        vertical_policy = (
            (
                QSizePolicy.Policy.Expanding
                if self.fill_available
                else QSizePolicy.Policy.Maximum
            )
            if expanded
            else QSizePolicy.Policy.Fixed
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, vertical_policy)
        arrow = (
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.toggle_button.setArrowType(arrow)
        self.updateGeometry()
