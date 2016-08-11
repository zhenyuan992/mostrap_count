"""works only on python 2

Two main mode, debug mode and operation mode.
Debug mode will bypass tis program and allow pi to boot normally
Operation mode will do the following.

follows the sequence in the photo
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
#setting up the variables used throughout the program
camera = PiCamera() #camera variables
camera.resolution = (2592,1994)##(64,64) ##
camera.framerate = 5
operationmode=False #mode variables
hasWifi=False #wifi variables
datafile_path = '/home/pi/Desktop/mostrap1pics/datafile'
texts_from_file=[]
newpicture = False
name_of_picture=' '
uploaded = False
rebooting = True
# OAuth2 access token.  TODO: login etc.
TOKEN = 'SDyNJ4RmLfUAAAAAAAByX0pAIy__azpL2s0Zl7VjYlbcEeBxX0OnlfhOcM5W6V2k'
parser = argparse.ArgumentParser(description='Sync /home/pi/Desktop/mostrap1pics to Dropbox')
parser.add_argument('folder', nargs='?', default='mostraptr_mini',
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
# **************************************************************************************
#DEF FUNCITONS *************************************************************************
# **************************************************************************************
def takepicture(value):
    """
    if have wifi,take picture name it with the date and time
    else name it the number.

    and finally store it into the local file
    """
    global camera
    global hasWifi
    global name_of_picture
    fmt = '%m-%d-%H-%M-%S'
    print('obtaining time')
    j=str(value)
    i = datetime.datetime.now(pytz.timezone('Asia/Singapore')).strftime(fmt)
    print(i)
    GPIO.output(20,GPIO.HIGH)
    sleep(0.5)
    print('starting camera')
    camera.start_preview()
    sleep(3) ##pauses for 3 seconds for camera to stablise
    if hasWifi:
        camera.capture('/home/pi/Desktop/mostrap1pics/mos%s.jpg' % i)
        name_of_picture="mos"+str(j)+"mos"+str(i)+".jpg"
    else:
        camera.capture('/home/pi/Desktop/mostrap1pics/mos%s.jpg' % j)
        name_of_picture="mos"+str(j)+".jpg"
    sleep(1)
    camera.stop_preview()
    sleep(0.5)
    GPIO.output(20,GPIO.LOW)
    print('a picture was taken')
    return True

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
    print('upload fully finished and sucessful')
    return True

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

def checkwifi(): #will change the value of hasWifi
    global hasWifi
    for counter in range(1,18):
        if connectwifi():
            print('wifi is good, setting has wifi to True')
            hasWifi=True
            break
        elif operationmode and counter>13:
            print('still no wifi, setting hasWifi to false')
            hasWifi=False
            break
        else:
            print('no WiFi, waiting for 10 sec then try again, max 2 mins')
            print("try number:")
            print(counter)
            sleep(10)

def connectwifi():
    print('checking for wifi conenction')
    try:
        hehehe=urllib2.urlopen('https://www.google.com',timeout=1)
        print('there is wifi')
        return True
    except urllib2.URLError as err: pass
    print('there is no wifi')
    return False

def rebootseq():
    camera.close()
    GPIO.cleanup()
    print('GPIO has been cleaned, pi is rebooting nowwww')
    os.system('sudo reboot')

def shutdownseq():
    camera.close()
    GPIO.output(21,GPIO.HIGH)
    sleep(7) #wait 10seconds then shutdown
    GPIO.output(21,GPIO.LOW)
    GPIO.cleanup()
    print('GPIO has been cleaned, pi is shutting down nowwww')
    os.system('sudo shutdown now -h')

def readfiletolines():
    textf=open(datafile_path,'r')
    global texts_from_file
    texts_from_file = textf.readlines()
    textf.close()

def textValueAdding(value): #value = -1 for rebootadding,-2 for upload
    global texts_from_file
    tobeedited=texts_from_file.pop()
    tobeedited=tobeedited.split(',')
    tobeedited[value]=" "+str(int(tobeedited[value])+1)+" "
    tobeedited = ",".join(tobeedited)
    texts_from_file.append(tobeedited)

def textAddLine(index,name,uploaded):
    global texts_from_file
    tobeadded="\n"+str(index)+" , "+name+" , "+str(uploaded)+" , 0 "
    texts_from_file.append(tobeadded)
    
def writetofile():
    global texts_from_file
    textf=open(datafile_path,'w')
    textf.writelines(texts_from_file)

@contextlib.contextmanager
def stopwatch(message):
    """Context manager to print how long a block of code took."""
    t0 = time.time()
    try:
        yield
    finally:
        t1 = time.time()
        print('Total elapsed time for %s: %.3f' % (message, t1 - t0))
# **************************************************************************************
#DEFINING FINISHED *********************************************************************
# **************************************************************************************

if __name__ == '__main__':
    print('starting mostrap program')
    try:
        print(' setting up gpio')
        setupgpio()
        checkmode()
    except:
        operationmode = False
        print(' something went wrong at gpio, defaulted to debug mode ')
        
    operationcomplete = False
    if operationmode:
        try:
            readfiletolines()
            iteminalist=texts_from_file[-1] #reads the last item
            value_from_last_item=iteminalist.split(',')
            index_last_item = int(value_from_last_item[0])
            name_last_item = str(value_from_last_item[1])
            last_item_uploaded = int(value_from_last_item[2])
            times_of_reboot = int(value_from_last_item[3])
            checkwifi() #haswifi will be set inside here
            if last_item_uploaded:
                print 'last item uploaded, proceed with'
                newpicture=takepicture(index_last_item+1)
                #takepicture(with index_last_item++) = newpicture
                textAddLine(index_last_item+1,name_of_picture,0)
            elif times_of_reboot > 5:
                print 'rebooted too many times, take new picture'
                newpicture=takepicture(index_last_item+1)
                #take picture (with index_last_item++) = newpicture
                textAddLine(index_last_item+1,name_of_picture,0)
            elif not hasWifi:
                print ' system has to reboot'
                rebooting = True

            if hasWifi:
                uploaded = mainsyncprogram()
                rebooting = not uploaded # upload fail, then it will try to reboot
            if (times_of_reboot > 5): # no matter what, if it reboots 5 times, its time to shutdown
                rebooting = False
            operationcomplete = True
        except:
            print('something went wrong in the main try')
            print(sys.exc_info()[0])
            sleep(2)
            #rebootseq()
        finally:
            if operationcomplete:
                #only happens when no exceptions is detected
                if uploaded or (not rebooting):
                    #write to file
                    if uploaded:
                        textValueAdding(-2)
                    writetofile()
                    shutdownseq()
                    print 'sys is shutting down'
                else:
                    #times_of_reboot++, write to file
                    textValueAdding(-1)
                    writetofile()
                    rebootseq()
                    print 'rebooting seq for real'
            else:
                print('exceptions not caught, please debug if you see this')
                rebootseq() #exceptions did not do its job

    else: ##entering into debug mode
        print('entering debug mode')
        camera.close()
        print('closing mostrap program and continuing with normal boot')

