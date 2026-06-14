from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Static


class WizardApp(App):
    TITLE = "artmind wizard"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("artmind wizard — coming soon")
        yield Footer()


def run_wizard() -> None:
    WizardApp().run()
