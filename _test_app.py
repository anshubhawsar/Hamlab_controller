import os
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from hamlab_v2_3 import ProHMI, _COOLING_AVAILABLE
    print("COOLING_AVAILABLE:", _COOLING_AVAILABLE)

    app = ProHMI()
    print("APP_CREATED")

    # Schedule a check then close
    def check_and_close():
        try:
            print("frame_cooling is None?", app.frame_cooling is None)
            if app.frame_cooling is not None:
                print("has btn_manual:", hasattr(app.frame_cooling, "btn_manual"))
                print("has sld_pwm:", hasattr(app.frame_cooling, "sld_pwm"))
            app._on_close()
        except Exception:
            traceback.print_exc()

    app.after(500, check_and_close)
    app.after(1200, app.destroy)
    app.mainloop()
    print("APP_DONE")
except Exception:
    traceback.print_exc()
    print("APP_FAIL")
