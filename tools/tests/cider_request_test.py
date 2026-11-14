"""USB request scheduling vs the local store is covered by cider_pin_test.py
(miss -> immediate cr, store hit -> held until HOLD_*, then raced)."""
import runpy,sys
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name('cider_pin_test.py')),run_name='__main__')
