"""works only on python 2

Two main mode, debug mode and operation mode.
Debug mode will bypass tis program and allow pi to boot normally
Operation mode will do the following.

-Check for WiFi / connect to WiFi.
-Take picture and name it with the current time.
And save it locally.

-Upload/sync folder to Dropbox (or Rsync)
-Shutdown procedure

functions present:
setupgpio() #run once to setup the gpios
checkmode() #run once to check for mode, operationmode = True or False
takepicture()
trygpio()
checkwifi()
mainsyncprogram() #the uploading/file sync sequence
cutoffgpio() #run once within shutdown seq
shutdownseq() #only happens in operationmode
rebootseq() #only happens in operationmode

"""
from picamera import PiCamera
from time import sleep

import RPi.GPIO as GPIO
import tty
import argparse
import contextlib
import datetime
import os
import six
import sys
import time
import unicodedata
import datetime
import pytz
import urllib2

if sys.version.startswith('2'):
    input = raw_input
import dropbox
from dropbox.files import FileMetadata, FolderMetadata

camera = PiCamera()
camera.resolution = (2592,1994)##(64,64) ##
camera.framerate = 5
operationmode=False
# OAuth2 access token.  TODO: login etc.
TOKEN = 'SDyNJ4RmLfUAAAAAAAByX0pAIy__azpL2s0Zl7VjYlbcEeBxX0OnlfhOcM5W6V2k'
parser = argparse.ArgumentParser(description='Sync /home/pi/Desktop/mostrap1pics to Dropbox')
parser.add_argument('folder', nargs='?', default='mostraptry',
                    help='Folder name in your Dropbox')
parser.add_argument('rootdir', nargs='?', default='/home/pi/Desktop/mostrap1pics',
                    help='Local directory to upload')
parser.add_argument('--token', default=TOKEN,
                    help='Access token '
                    '(see https://www.dropbox.com/developers/apps)')
parser.add_argument('--yes', '-y', action='store_true',
                    help='Answer yes to all questions')
parser.add_argument('--no', '-n', action='store_true',
                    help='Answer no to all questions')
parser.add_argument('--default', '-d', action='store_true',
                    help='Take default answer on all questions')

def takepicture():
    """take picture name it with the date and time
    and store it into the local file
    """
    global camera

    fmt = '%Y-%m-%d-%H-%M-%S'
    print('obtaining time')
    i = datetime.datetime.now(pytz.timezone('Asia/Singapore')).strftime(fmt)
    print(i)
    print('starting camera')
    camera.start_preview()
    sleep(3) ##pauses for 3 seconds for camera to stablise
    camera.capture('/home/pi/Desktop/mostrap1pics/mos%s.jpg' % i)
    sleep(1)
    camera.stop_preview()
    print('a picture was taken')

def setupgpio():
    GPIO.setmode(GPIO.BCM)
    GPIO.setwarnings(False)
    GPIO.setup(20,GPIO.OUT)
    GPIO.setup(21,GPIO.OUT)
    GPIO.output(21,GPIO.LOW) #tells arduino to not reset the power
    GPIO.setup(16,GPIO.IN) #input for shutdown, high to operation (will shutdown), low to debug

def checkmode():
    global operationmode
    operationmode = GPIO.input(16) #detects the input of 16

def trygpio():
    """
    Pin input and output at raspberry pi
    Most important pin is the pin 16 which feeds to arduino
    """
    GPIO.output(20,GPIO.LOW)
    if GPIO.input(16):
    else:
        GPIO.cleanup()
        print('im not shutting down')

def mainsyncprogram():
    """Main program.
    This handles the uploading part, makes use of "upload()" function.

    Parse command line, then iterate over files and directories under
    rootdir and upload all files.  Skips some temporary files and
    directories, and avoids duplicate uploads by comparing size and
    mtime with the server.
    """

    args = parser.parse_args()
    if sum([bool(b) for b in (args.yes, args.no, args.default)]) > 1:
        print('At most one of --yes, --no, --default is allowed')
        sys.exit(2)
    if not args.token:
        print('--token is mandatory')
        sys.exit(2)

    folder = args.folder
    rootdir = os.path.expanduser(args.rootdir)
    print('Dropbox folder name:', folder)
    print('Local directory:', rootdir)
    if not os.path.exists(rootdir):
        print(rootdir, 'does not exist on your filesystem')
        sys.exit(1)
    elif not os.path.isdir(rootdir):
        print(rootdir, 'is not a foldder on your filesystem')
        sys.exit(1)

    dbx = dropbox.Dropbox(args.token)

    for dn, dirs, files in os.walk(rootdir):
        subfolder = dn[len(rootdir):].strip(os.path.sep)
        listing = list_folder(dbx, folder, subfolder)
        print('Descending into', subfolder, '...')

        # First do all the files.
        for name in files:
            fullname = os.path.join(dn, name)
            if not isinstance(name, six.text_type):
                name = name.decode('utf-8')
                print(name)
            nname = unicodedata.normalize('NFC', name)
            if name.startswith('.'):
                print('Skipping dot file:', name)
            elif name.startswith('@') or name.endswith('~'):
                print('Skipping temporary file:', name)
            elif name.endswith('.pyc') or name.endswith('.pyo'):
                print('Skipping generated file:', name)
            elif nname in listing:
                md = listing[nname]
                mtime = os.path.getmtime(fullname)
                mtime_dt = datetime.datetime(*time.gmtime(mtime)[:6])
                size = os.path.getsize(fullname)
                if (isinstance(md, dropbox.files.FileMetadata) and
                    mtime_dt == md.client_modified and size == md.size):
                    print(name, 'is already synced [stats match]')
                else:
                    print(name, 'exists with different stats, downloading')
                    res = download(dbx, folder, subfolder, name)
                    with open(fullname) as f:
                        data = f.read()
                    if res == data:
                        print(name, 'is already synced [content match]')
                    else:
                        print(name, 'has changed since last sync')
                        if yesno('Refresh %s' % name, False, args):
                            upload(dbx, fullname, folder, subfolder, name,
                                   overwrite=True)
            elif yesno('Upload %s' % name, True, args):
                upload(dbx, fullname, folder, subfolder, name)

        # Then choose which subdirectories to traverse.
        keep = []
        for name in dirs:
            if name.startswith('.'):
                print('Skipping dot directory:', name)
            elif name.startswith('@') or name.endswith('~'):
                print('Skipping temporary directory:', name)
            elif name == '__pycache__':
                print('Skipping generated directory:', name)
            elif yesno('Descend into %s' % name, True, args):
                print('Keeping directory:', name)
                keep.append(name)
            else:
                print('OK, skipping directory:', name)
        dirs[:] = keep
    print('upload fully finished')


def list_folder(dbx, folder, subfolder):
    """List a folder.

    Return a dict mapping unicode filenames to
    FileMetadata|FolderMetadata entries.
    """
    path = '/%s/%s' % (folder, subfolder.replace(os.path.sep, '/'))
    while '//' in path:
        path = path.replace('//', '/')
    path = path.rstrip('/')
    try:
        with stopwatch('list_folder'):
            res = dbx.files_list_folder(path)
    except dropbox.exceptions.ApiError as err:
        print('Folder listing failed for', path, '-- assumped empty:', err)
        return {}
    else:
        rv = {}
        for entry in res.entries:
            rv[entry.name] = entry
        return rv

def download(dbx, folder, subfolder, name):
    """Download a file.

    Return the bytes of the file, or None if it doesn't exist.
    """
    path = '/%s/%s/%s' % (folder, subfolder.replace(os.path.sep, '/'), name)
    while '//' in path:
        path = path.replace('//', '/')
    with stopwatch('download'):
        try:
            md, res = dbx.files_download(path)
        except dropbox.exceptions.HttpError as err:
            print('*** HTTP error', err)
            return None
    data = res.content
    print(len(data), 'bytes; md:', md)
    return data

def upload(dbx, fullname, folder, subfolder, name, overwrite=False):
    """Upload a file.

    Return the request response, or None in case of error.
    """
    path = '/%s/%s/%s' % (folder, subfolder.replace(os.path.sep, '/'), name)
    while '//' in path:
        path = path.replace('//', '/')
    mode = (dropbox.files.WriteMode.overwrite
            if overwrite
            else dropbox.files.WriteMode.add)
    mtime = os.path.getmtime(fullname)
    with open(fullname, 'rb') as f:
        data = f.read()
    with stopwatch('upload %d bytes' % len(data)):
        print('starting upload')
        try:
            res = dbx.files_upload(
                data, path, mode,
                client_modified=datetime.datetime(*time.gmtime(mtime)[:6]),
                mute=True)
        except dropbox.exceptions.ApiError as err:
            print('*** API error', err)
            return None
    print('uploaded as', res.name.encode('utf8'))
    return res

def yesno(message, default, args):
    print(message + '? [auto] YES')
    return True

def checkwifi():
    print('checking for wifi conenction')
    try:
        hehehe=urllib2.urlopen('http://www.google.com',timeout=1)
        print('there is wifi')
        return True
    except urllib2.URLError as err: pass
    print('there is no wifi')
    return False

def rebootseq():
    GPIO.cleanup()
    print('GPIO has been cleaned, pi is rebooting nowwww')
    os.system('sudo reboot')

def shutdownseq():
    GPIO.output(21,GPIO.HIGH)
    sleep(7) #wait 10seconds then shutdown
    GPIO.output(21,GPIO.LOW)
    GPIO.cleanup()
    print('GPIO has been cleaned, pi is shutting down nowwww')
    os.system('sudo shutdown now -h')

@contextlib.contextmanager
def stopwatch(message):
    """Context manager to print how long a block of code took."""
    t0 = time.time()
    try:
        yield
    finally:
        t1 = time.time()
        print('Total elapsed time for %s: %.3f' % (message, t1 - t0))

if __name__ == '__main__':
    print('starting mostrap program')
    setupgpio()
    checkmode()
    operationcomplete = False
    if operationmode:
        try:
            if checkwifi():
                print('wifi is good')
            else:
                print('waiting for 5 sec then try again')
                sleep(5)
                if checkwifi():
                    print('wifi is good')
                elif operationmode:
                    print('still no wifi, rebooting')
                    rebootseq()
            takepicture()
            mainsyncprogram()
            operationcomplete = True
        except:
            print('something went wrong rebooting in 2 seconds')
            sleep(2)
            rebootseq()
        finally:
            if operationcomplete:
                shutdownseq() #only happens when no exceptions is detected
            else:
                print('exceptions not caught, please debug if you see this')
                rebootseq() #exceptions did not do its job

    else:
        print('closing mostrap program and continuing with normal boot')
    #with shutdown
    #process to shutdown the raspi
        #pin high
        #shutdown
