"""Desktop shell for Journal Typesetting Studio.

Runs the same local web interface (`studio.web`) inside its own window, so an
editor opens the Studio from a desktop icon instead of a terminal. Nothing in
the production logic changes: the window is a view onto 127.0.0.1.

    python -m studio.desktop            # open the Studio window
    python -m studio.desktop --browser  # use the default browser instead
"""
