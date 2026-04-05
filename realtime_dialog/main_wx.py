from wx_app import RealtimeDialogApp


def main() -> None:
    app = RealtimeDialogApp(False)
    app.MainLoop()


if __name__ == "__main__":
    main()
