from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QWidget


def dispose_widget(widget: QWidget) -> None:
    """Destroy the native widget on the GUI thread before other tests start."""
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)
