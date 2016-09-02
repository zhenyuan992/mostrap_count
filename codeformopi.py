import os
import sys
import time
#from daemon import Daemon
import sys
sys.path.append("/pi/Desktop/")
import mopiapi

mopi = mopiapi.mopiapi()

mopi.setPowerOnDelay(60)

mopi.setShutdownDelay(5)
