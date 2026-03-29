import sys
from region_renderers import render_nutrition
import inspect

print("MIN_FS def line:", inspect.getsourcelines(render_nutrition)[0][50:56])
