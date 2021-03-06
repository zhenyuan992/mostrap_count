#!/usr/bin/env python

import os
import sys
import time
from daemon import Daemon
import syslog
import traceback
import picamera
import datetime
import sunrise
import subprocess
import decimal
import shutil
import errno
import pickle
from collections import namedtuple
import ConfigParser

### XXX temp - until mopiapi in right directory
sys.path.append("/usr/sbin/")
### XXX temp
import mopiapi

def roundTime(dt=None, roundTo=60*30):
   """Round a datetime object to any time laps in seconds
   dt : datetime.datetime object, default now.
   roundTo : Closest number of seconds to round to, default 30 minutes.
   Author: Thierry Husson 2012 - Use it as you want but don't blame me.
   From: http://stackoverflow.com/questions/3463930/how-to-round-the-minute-of-a-datetime-object-python/10854034#10854034
   """
   if dt == None : dt = datetime.datetime.now()
   seconds = (dt - dt.min).seconds
   # // is a floor division, not a comment on following line:
   rounding = (seconds+roundTo/2) // roundTo * roundTo
   return dt + datetime.timedelta(0,rounding-seconds,-dt.microsecond)

class TempusFugit(Daemon):
    """Raspberry Pi time lapse daemon
    Designed to be run at startup on a Raspberry Pi running a MoPi power
    monitor - such that we will be started once the RPi is up and running,
    we'll do our work, and then ask MoPi to turn RPi off and wake up again
    later.  Much like a hardware cron job.
    This is designed with a network-unattahed RPi model A running off batteries
    - this will give the longest possible battery life for our time lapse, but
    to get sane dates on the photos we'll want a hardware RTC connected too.
    The device is likely to be run remotely to the person who knows how to fix
    it, so the aim is that everything can be run without needing an attached
    monitor or any great intelligence.  The device will snap pictures away
    at the configured times, and when a USB drive is plugged in it will update
    itself and copy off any photos taken.  Nice and simple.
    The work to do on each power up is:
    - if a USB drive is found, copy over any new configuration
     (this is useful as we are running remotely and not network connected)
    - take a photo!
    - if a USB drive is found, copy off any photos and log files (eg. to
     indicate power state)
    """
    MY_NAME = "tempus_fugit"
    UPDATE_DIR = "update/"
    OUTPUT_DIR = "/home/pi/%s/photos/" % (MY_NAME)
    USBDRIVE_MOUNTPOINT = "/media/usb0/"
    USBDRIVE_CONTROL_DIR = os.path.join(USBDRIVE_MOUNTPOINT, "%s/" % MY_NAME)
    USBDRIVE_PHOTO_DIR = os.path.join(USBDRIVE_MOUNTPOINT, "photos/")
    ISO_TIME_FMT = "%Y%m%dT%H%M%S"
    MAGIC_FILE_BLOCK_RELOAD = "block_reload"
    MAGIC_FILE_TAKE_PHOTO = "force_photo"
    MAGIC_FILE_SAVED_FILES = "saved_files"
    MAGIC_FILE_DELETE_FILES = "saved_files.done"
    MAGIC_FILE_CONFIG_NEW = "tempus_fugitd.cfg"
    MAGIC_FILE_CONFIG_ACTIVE = "/etc/tempus_fugitd"
    REQUIRED_CONFIG = set(["name", "latitude", "longitude", "resolution"])
    OPTIONAL_CAMERA_CONFIG = ["awb_mode", "brightness", "contrast", "exposure_compensation", "exposure_mode", "meter_mode", "rotation", "saturation", "sharpness", "shutter_speed"]
    OPTIONAL_CONFIG = [].extend(OPTIONAL_CAMERA_CONFIG)

    def __init__(self, *args, **kwargs):
        self.config = None
        self.do_update = False
        self.have_usb = False
        self.block_reload = False
        self.force_photo = False
        self.hwclock = False
        try:
            os.mkdir(self.OUTPUT_DIR)
        except OSError:
            if not os.path.exists(self.OUTPUT_DIR):
                raise

        super(TempusFugit, self).__init__(*args, **kwargs)

    @staticmethod
    def log(*args):
        if len(args) == 1:
            if isinstance(args[0], str):
                # Args are a single string (which might have been a format
                #  string that is now calculated)
                msg = str(args[0])
            else:
                # Args are a single item but not a string - do our best to
                # output (note: this catches cases where we have wrong number
                # of args for format!)
                msg = str(args[0])
        else:
            # Args are a bunch of random items, stringify as best we can.
            msg = str(args)
        syslog.syslog(msg)
    @staticmethod
    def logerr(msg):
        syslog.syslog(syslog.LOG_ERR, msg)

    @staticmethod
    def decimal_location_to_exif_gps(val_dec, latitude=True):
        """Convert a decimal longitude or latitude to a string
        suitable for use in EXIF GPS data (which for eg. 51 degrees, 49', 0''
        would be "51/1,49/1,0/1")
        """
        if val_dec > 0:
            if latitude:
                ref = "N"
            else:
                ref = "E"
        else:
            if latitude:
                ref = "S"
            else:
                ref = "W"
            val_dec = -val_dec
        degree = int(val_dec)
        val_dec -= degree
        val_dec *= 60
        minute = int(val_dec)
        val_dec -= minute
        val_dec *= 60
        second = int(val_dec)
        return ("%u/1,%u/1,%u/1" % (degree, minute, second), ref)

    def _pause_for_systime(self):
        """Block waiting for system to have a valid time (ie. not just the time
        that was stored on last power down) - which means either:
        - getting a time from a hardware clock if present
        - waiting for NTP to have a valid peer, if no RTC is present (we assume
          that as soon as we have a valid peer the system time will be correct,
          as RPi is using '-g' option)
        Returns the time it took to get a valid system time (which could be
        significant if ntpd method is being used)
        """
        def uptime():
            with open('/proc/uptime', 'r') as f:
                return decimal.Decimal(f.readline().split()[0])

        start_time = uptime()

        if subprocess.call(["hwclock", "-r"]) == 0:
            self.hwclock = True
            return uptime() - start_time

        while True:
            for line in subprocess.check_output(["ntpdc", "-c sysinfo"], stderr=subprocess.STDOUT).split("\n"):
                if line.startswith("system peer:"):
                    peer = line.replace("system peer:", "")
                    peer = peer.strip()
                    if peer == "0.0.0.0":
                        self.log("Peer unknown")
                    else:
                        self.log("NTP peer found - stop")
                        return uptime() - start_time
            self.log(".")
            time.sleep(0.1)

    def _load_and_validate_config(self, filename):
        """Load config from the specified file, and do some validation:
        - check ConfigParser can read it!
        - check the minimum required set of fields are present
        - check those fields are valid
        Any other config fields (if any) will not be validated here - as they
        are optional they can be validated when needed and ignored if not
        valid.
        """
        config = ConfigParser.ConfigParser()
        config.read(filename)
        if not self.REQUIRED_CONFIG.issubset(set(config.defaults())):
            self.log("Missing required config: %s" % (self.REQUIRED_CONFIG - set(config.defaults())))
            raise ValueError
        fields = {}

        # name - no validation
        fields["name"] = config.get("DEFAULT", "name")

        # resolution - either "MAX" or wxh (where w and h are integers)
        resolution = config.get("DEFAULT", "resolution")
        if resolution == "MAX":
            fields["resolution"] = "MAX"
        else:
            try:
                width, height = map(int, resolution.split("x"))
                fields["resolution"] = (width, height)
            except ValueError:
                # This will catch both int() conversion issues, and incorrect
                # number of values in unpacking.
                self.log("Specified resolution (%s) is invalid - ignoring config file" % (resolution))
                raise

        # latitude and longitude
        try:
            latitude_str = config.get("DEFAULT", "latitude")
            longitude_str = config.get("DEFAULT", "longitude")
            latitude = float(latitude_str)
            longitude = float(longitude_str)
            if latitude > 90 or latitude < -90 or longitude > 180 or longitude < -180:
                raise ValueError
            fields["latitude"] = latitude
            fields["longitude"] = longitude
        except ValueError:
            self.log("Specified longitude (%s) or latitude (%s) is invalid - ignore config" % (longitude_str, latitude_str))
            raise

        # All mandatory config is valid, so store it!
        self.__dict__.update(fields)
        self.log("Config read ok - required config: %s" % (fields))
        
        return config

    def _find_usbdrive(self, mountpoint=USBDRIVE_MOUNTPOINT):
        """Look for an attached USB drive.  We are using "usbmount" to handle
        usb drive insertion, so it should be as simple as seeing if there is
        anything under /media/usb0 (as we should only ever have a single device
        connected on a single USB port Model A!
        """
        if os.path.ismount(mountpoint):
            self.have_usb = True
            if os.path.exists(os.path.join(mountpoint, self.UPDATE_DIR)):
                self.do_update = True
            if os.path.exists(os.path.join(mountpoint, self.USBDRIVE_CONTROL_DIR, self.MAGIC_FILE_BLOCK_RELOAD)):
                self.block_reload = True
            if os.path.exists(os.path.join(mountpoint, self.USBDRIVE_CONTROL_DIR, self.MAGIC_FILE_TAKE_PHOTO)):
                self.force_photo = True
            new_config_filename = os.path.join(mountpoint, self.USBDRIVE_CONTROL_DIR, self.MAGIC_FILE_CONFIG_NEW)
            if os.path.exists(new_config_filename):
                try:
                    self.config = self._load_and_validate_config(new_config_filename)
                    shutil.copyfile(new_config_filename, self.MAGIC_FILE_CONFIG_ACTIVE)
                    os.rename(new_config_filename, new_config_filename + ".done")
                    self.log("Config updated from USB drive")
                except:
                    self.log("Failed to parse new config file, ignoring")
    def _take_photo(self):
        """Do the work of taking a photo
        EXIF data is modified to add:
        - copyright notice (currently my name and current year; config should
          allow user to be specified)
        - GPS location (which is obviously hardcoded as we have no GPS!)
        Config is read to determine any modifications to camera settings
        (exposure for example), with a photo taken with and without these settings
        (just in case someone sets it up to take a terrible photo!)
        """
        fname = "%s.jpg" % (datetime.datetime.today().strftime(self.ISO_TIME_FMT))
        fname = os.path.join(self.OUTPUT_DIR, fname)
        fname_orig = fname.replace(".jpg", "_original.jpg")
        with picamera.PiCamera() as camera:
            # Setup the camera with all the settings that can't cause a photo
            # to come out terribly - and take one photo with those settings
            camera.led = False # Turn off LED to avoid reflection shooting through glass
            if self.resolution == "MAX":
                camera.resolution = camera.MAX_RESOLUTION
            else:
                try:
                    camera.resolution = self.resolution
                except:
                    self.log("Resolution specified in configuration is invalid (%s) - set to MAX" % (str(self.resolution)))
                    camera.resolution = camera.MAX_RESOLUTION
            camera.exif_tags["IFD0.Copyright"] = 'Copyright (c) %s %s' % (datetime.datetime.now().year, self.name)

            location, ref = self.decimal_location_to_exif_gps(self.latitude, True)
            camera.exif_tags["GPS.GPSLatitudeRef"] = ref
            camera.exif_tags["GPS.GPSLatitude"] = location

            location, ref = self.decimal_location_to_exif_gps(self.longitude, False)
            camera.exif_tags["GPS.GPSLongitudeRef"] = ref
            camera.exif_tags["GPS.GPSLongitude"] = location

            # First take a photo with the unmodified settings (ie. most things on auto)
            camera.capture(fname_orig)

            take_modified_photo = False
            # Now apply the config settings and take a second photo
            for attr in self.OPTIONAL_CAMERA_CONFIG:
                if not attr in self.config.defaults():
                    continue
                val = self.config.get("DEFAULT", attr)

                try:
                    # picamera is fussy about types - can't be an int pretending to be a string
                    val = int(val)
                except ValueError:
                    pass

                try:
                    old_val = camera.__getattribute__(attr)
                    camera.__setattr__(attr, val)
                    self.log("Camera: set attr '%s' to value '%s' (old value '%s')" % (attr, val, old_val))
                    take_modified_photo = True
                except picamera.exc.PiCameraValueError:
                    self.log("Camera: FAILED to set attr '%s' to value '%s' - ignoring (original value '%s')" % (attr, val, old_val))

            if take_modified_photo:
                camera.capture(fname)

    def _get_photo_archive_info(self, copy=True):
        """Return information on the locally stored photos, using the 
        information stored on the USB drive to indicate the last file copied.
        'copy' argument shows which file to look for - True for copy
        operations, False for delete operations (remember that the user
        manually renames the magic file to indicate that files previously
        copied to USB drive have been verified and safely backed up
        Returns a namedtuple containing:
        - last_filename_copied: the name of the last file copied from local
          store to USB drive
        - archived: a list of files (in increasing date order) up to and
          including last_filename_copied.  For delete operations this indicates
          all the files that have been copied to USB and that the user has
          indicated are safe to delete.  For copy operations this indicates the
          files that have already been copied to USB drive (but not necessarily
          saved elsewhere yet)
        - unarchived: a list of files (in increasing date order) that are newer
          than last_filename_copied.  For delete operations this indicates
          files that have not been archived yet (and so must not be deleted
          locally!).  For copy operations this indicates files that have not
          been copied to USB drive yet.
        """
        magic_fname = self.MAGIC_FILE_SAVED_FILES if copy else self.MAGIC_FILE_DELETE_FILES
        fname = os.path.join(self.USBDRIVE_CONTROL_DIR, magic_fname)

        if os.path.exists(fname):
            with open(fname) as f:
                last_filename_copied = pickle.load(f)
            if not copy:
                # As we are about to delete all the files already copied, delete
                # the control file - the deletes can't realistically fail (and if it could then we'll just recopy those files)
                os.remove(fname)

        else:
            last_filename_copied = None # all files will be considered unarchived compared to this

        # File name is YYYYMMDDTHHMMSS.jpg - so sorting will give us files
        # in increasing date order
        files = os.listdir(self.OUTPUT_DIR)
        files.sort()

        if last_filename_copied and not last_filename_copied in files:
            self.logerr("Last saved file '%s' is not found in local store - assume all files are unarchived, as something is very wrong" % (last_filename_copied))
            last_filename_copied = None

        return namedtuple("photoarchive", "last_filename_copied archived unarchived")(
            last_filename_copied,
            [x for x in files if x <= last_filename_copied],
            [x for x in files if x > last_filename_copied])

    def _remove_old_files(self):
        """Remove the files that have been archived elsewhere
        The interface for this is pretty simple - when we copy files we do
        this in age order (stopping if we run out of space), and store the
        details of the last file copied over.  If the user wants the files to be deleted then they simply rename the file to indicate that.
        This copes with several situations:
        - USB drive left plugged in indefinitely.  Until the magic file appears
          we will not remove any files from our local store, and will only copy
          over until we've run out of space (at which point we'll still store
          photos locally, but not on the USB drive)
        - The user inserts a fresh USB drive (after having used a different
          drive previously, but without deleting the locally stored copies
          of those files).  We fail safe in this case and assume the previously
          copied files are gone - and so recopy everything again.  It is up to
          the user to deal with any duplication here - which should be easy due
          to timestamps in filenames.
        - USB drive plugged in for the first time.  This will behave exactly
          like the previous case!
        - The user deletes an old magic file.  This is the same as the fresh
          USB drive case ie. we assume that nothing local has been copied
          and let the user sort out any duplication.
        """
        info = self._get_photo_archive_info(copy=False)
        if info.last_filename_copied:
            os.chdir(self.OUTPUT_DIR)
            archived = info.archived if info.archived else ["none", "none"]
            unarchived = info.unarchived if info.unarchived else ["none", "none"]
            self.log(
                "Deleting %s files up to and including %s; leaving %s files "
                "[delete: %s-%s; keep: %s-%s]" % 
                (len(info.archived), info.last_filename_copied,
                 len(info.unarchived), archived[0], archived[-1],
                 unarchived[0], unarchived[-1]))
            for file in info.archived:
                os.remove(file)

            # The record of files to delete has already been removed by worker above
            # No need to create a new last_filename_copied control file - as
            # we'll just copy everything if it isn't there, as we want.
        else:
            self.log("No delete file found, no files to delete")

    def _write_readme_file(self):
        def full_usbdrive_path(file):
            return os.path.join(self.MY_NAME, file)
        instructions = """Instructions for tempus_fugit program
The time lapse module will sit there and turn itself on once every 1/2 hour
between sunrise and sunset, snapping a picture each time.
If a USB drive is found when the camera powers on (manually or due to its
timer) then photos will be copied off (as well as doing any necessary
house keeping)
If the time lapse is turned on manually then it will do any house keeping
necessary (archive images, delete "seen" images, update config, etc.) but will
not ordinarily take a photo.
1) Setup
- this assumes all scripts are already installed
- create a config file, see here for a sample:
  https://github.com/stoduk/tempus_fugit/blob/master/tempus_fugitd.cfg
- save the config file in the root of the USB drive, with the name
  %s
- insert the USB drive, power on the camera
2) Changing batteries
Any time the batteries are disconnected from the camera this will make the
camera forget to turn itself on again.  So after the batteries are
disconnected the camera should be powered on so that the right decisions
and scheduling can be done.
It is safe to turn the camera on manually at any time, the only impact is on
the battery levels!
3) Archiving photos
The camera stores photos to its local storge, but will copy them off whenever
a USB drive is inserted.  It will *not* delete the locally stored files until
it is told to do so - this should only be done once the photos have been copied
elsewhere (eg. uploaded to Flickr) to avoid photo loss.
- any time a USB drive is present when the camera is powered on (either from
its timer or manually) then it will copy across as many photos as it can.
- once the photos on the USB drive have been archived elsewhere, the camera
can be told to delete them.  This is done by renaming the file named
  %s
to
  %s
3) Debugging
If the following magic files exist then certain behaviour is forced - this
will mostly be for debugging purposes, or initial setup testing:
a) %s
If this file exists on the USB drive then the camera will not shut down after
its work is done.  Only needed for debugging.
b) %s
If this file exists on the USB drive then it forces the camera to take a
photo even if it doesn't think it is time
c) %s
This will contain syslog from the camera, useful for debugging problems.
""" % tuple(map(full_usbdrive_path,
       [self.MAGIC_FILE_CONFIG_NEW, self.MAGIC_FILE_SAVED_FILES,
        self.MAGIC_FILE_DELETE_FILES, self.MAGIC_FILE_BLOCK_RELOAD,
        self.MAGIC_FILE_TAKE_PHOTO, "syslog"]))
        if self.have_usb:
            fname = os.path.join(self.USBDRIVE_MOUNTPOINT, "README.txt")
            if os.path.exists(fname) and not "tempus_fugit" in open(fname).readline():
                self.logerr("Readme file exists but doesn't appear to be ours - do nothing")
            else:
                with open(fname, "w") as f:
                    f.write(instructions)

    @staticmethod
    def free_space_mb(path):
        # Return the free space for the specified volume mounted at path
        stat = os.statvfs(path)
        return int(stat.f_bsize * stat.f_bavail / 1024. / 1024.)
                
    def _copy_files(self):
        """At some level this is simple - copy all photos and logs to the
        attached USB drive.
        The complexity appears because we want to have a way to specify that
        (on a later USB insert) that "seen" files should be deleted - we
        require manual intervention here to make sure that these files have
        been backed up somewhere before we delete them
        We force the manual step to avoid the case where a USB drive remains
        plugged in but somehow gets corrupted - we don't want to remove
        the local copy of files until we are sure the files on the USB drive
        have been seen and verified.
        So basically we create a file in the target directory containing
        (a pickled) list of files that have been copied - when the user has
        checked these copied correctly they can rename that file to have all
        photos referenced in it deleted.  Obviously the names of the original
        and renamed pickle files are well known.
        """
        info = self._get_photo_archive_info(copy=True)
        fs_before = self.free_space_mb(self.USBDRIVE_MOUNTPOINT)

        for dir in (self.USBDRIVE_CONTROL_DIR, self.USBDRIVE_PHOTO_DIR):
            if not os.path.exists(dir):
                os.mkdir(dir)

        try:
            # Swallow out of space error for this, syslog large so likely!
            shutil.copy("/var/log/syslog", self.USBDRIVE_CONTROL_DIR)
        except IOError as e:
            if e.errno != errno.ENOSPC:
                raise

        # Rotating logfiles can be done as often as we want,
        # the config file determines how often something happens
        if subprocess.call(["/usr/sbin/logrotate", "/etc/logrotate.conf"]) != 0:
            self.log("Logrotate failed - continue regardless")

        os.chdir(self.OUTPUT_DIR)

        last_copied = info.last_filename_copied
        for file in info.unarchived:
            try:
                shutil.copy(file, self.USBDRIVE_PHOTO_DIR)
                last_copied = file
            except IOError as e:
                # Out of space error is expected so swallow just that.  Sadly
                # I get a different error on the USB drive I was using
                #  (errno.EINTR) so swallow all IOError and tidy up as best we
                # can - if the drive is totally hosed then the cleanup (or 
                # pickle file write) will bail out in a similar way so we'll
                # see that
                # 
                # Helpfully shutil.copy will leave a partially copied file,
                # so lets tidy up
                fname = os.path.join(self.USBDRIVE_PHOTO_DIR, file)
                os.remove(fname)

        copied = [x for x in info.unarchived if x <= last_copied]
        uncopied = [x for x in info.unarchived if x > last_copied]
        copied_count = len(copied)
        uncopied_count = len(uncopied)
        if not copied:
            copied = ["none", "none"]
        if not uncopied:
            uncopied = ["none", "none"]

        fs_after = self.free_space_mb(self.USBDRIVE_MOUNTPOINT)
        self.log(
            "Copying files: %s available to copy after file %s; %s copied; %s "
            "uncopied [copied %s-%s, uncopied %s-%s]; "
            "free space before %sMb, after %sMb" % 
            (len(info.unarchived), info.last_filename_copied, copied_count,
             uncopied_count, copied[0], copied[-1], uncopied[0], uncopied[-1],
             fs_before, fs_after))

        # Store the name of the last file we saved, so we can resume next time
        pickle.dump(last_copied, open(os.path.join(self.USBDRIVE_CONTROL_DIR, self.MAGIC_FILE_SAVED_FILES), "w"))

    def _think_hard(self):
        """Work out what needs to be done, including:
        - whether we need to update our scripts or config.  Config
          updating it implemented (we check we can parse the new file, and 
          if so copy it across - all done earlier so no need for us to restart
          to stay sane).  Script updating is not yet implemented..
        - whether we need to take a photo
          We take a photo if we've been woken up by the timer (or if this is
          our first power on), but not if we've been woken up by a button
          press (which is used to trigger updates and offloading of photos)
        - whether we need to copy off photos and logs, and delete local copies
          We copy files off if there is a USB drive present - ordinarily we
          just copy files over, leaving a backup locally.  If there is a magic
          file on the USB drive however we will trigger removal of old photos
          (which obviously requires remembering which photos have been
          copied already!), and the same for log files.
        - when we need to schedule our next wake up
          Eventually I'll probably allow some way to specify exactly when
          to take photos (perhaps in some cron style syntax) - but for now
          just take a photo every 1/2 hour between sunrise and sunset.
        """

        # Look for the USB drive which can trigger interesting things
        # (including a new config file, which we want to validate/read before
        # any of the config items are needed)
        self._find_usbdrive()

        if os.path.exists("/run/wpa_supplicant/wlan0"):
            self.log("WLAN device attached, block reload - assuming debugging session")
            self.block_reload = True

        self.log("Have drive %s, do update %s, block reload %s" % (self.have_usb, self.do_update, self.block_reload))

        if not self.config:
            self.config = self._load_and_validate_config(self.MAGIC_FILE_CONFIG_ACTIVE)

        # Note: though we could just look at the current time to decide whether
        # this is a photo taking time that might not be very accurate as RPi's
        # view of time (via hardware RTC) and MoPi's view of time (via a simple
        # counter in microcontroller, I believe) are likely to differ.  So we'd
        # need to cope with being woken up a little early or a little late and
        # still do the right thing, and given we can also be woken up manually
        # that is likely to be complicated.
        #
        # So we just use the MoPi's power on delay value, which means we'll
        # at worst just take an extra photo when power is first turned on to
        # MoPi:
        #
        # If MoPi's power on delay is non-zero then we haven't been woken up by 
        # MoPi and so this is a manual startup (ie. a button press, to trigger 
        # copying from/to USB).  If the power on delay is zero then it could be 
        # that the timer expired or could be that the MoPi is being powered for 
        # the first time - treat both the same and take a picture now.
        self.mopi = mopiapi.mopiapi()
        pod =  self.mopi.getPowerOnDelay()
        voltage = self.mopi.getVoltage()

        if pod == 0:
            self.take_photo = True
        else:
            self.take_photo = False

        if self.force_photo:
            self.take_photo = True

        self.remove_old_files = self.have_usb
        self.copy_files = self.have_usb

        self.log("Actions: take photo %s (force flag set %s), copy files %s, remove old files %s" % (self.take_photo, self.force_photo, self.copy_files, self.remove_old_files))

        # For now we just take photos every 1/2 hour between sunrise and
        # sunset.  In future it might be nice to take extra photos around
        # sunrise/sunset but our startup time might not be very accurate
        # so it could be easier in that case to just stay powered on for the
        # first/last 15 minutes of light or so.
        s = sunrise.sun(lat=self.latitude, long=self.longitude, return_dates=True)
        now = datetime.datetime.now()
        srise = s.sunrise()
        sset = s.sunset()

        self.log("Running with %sMb free space locally at %s; sunrise %s; sunset %s" % (self.free_space_mb("/home/pi"), now, srise, sset))
        if now < srise or now > sset:
            # It doesn't matter why we are awake (manual intervention or some
            # timing error) - either way we'll wake when it is next light
            # (ie. sunrise either today or tomorrow)
            self.log("Woken at night! (now %s, sunrise %s, sunset %s, power on delay %s)" % (now, srise, sset, pod))
            if now < srise:
                target = srise
            else:
                target = s.sunrise(tomorrow=True)
            # Round *up* to the next 1/2 hour
            wakeup = roundTime(target)
            if wakeup < target:
                wakeup += datetime.timedelta(seconds=60*30)
        elif pod == 0:
            # We've been woken by timer (or first power up, which we cheat and
            # treat the same) - schedule next photo in half hour.
            # First round the current time to nearest 1/2 hour (in either direction)
            # as clock skew could make it seem that we've woken up "too early" or "too late"
            wakeup = roundTime(now) + datetime.timedelta(seconds=60*30)
        else:
            # we've been woken up manually, so our next wake up will be in less
            # than half hour - round *up* to next half hour
            target = now
            wakeup = roundTime(target)
            if wakeup < target:
                wakeup += datetime.timedelta(seconds=60*30)
        self.wakeup = wakeup
        self.log("Target wakeup time %s based on current time %s, power on delay %s [supply voltage %s]" % (self.wakeup, now, pod, voltage))

    def _schedule_wake_up(self):
        seconds = (self.wakeup - datetime.datetime.now()).seconds
        self.log("Setting wakeup in %s seconds, shut down immediately active %s" % (seconds, not self.block_reload))
        self.mopi.setPowerOnDelay(seconds)
        if not self.block_reload:
            self.mopi.setShutdownDelay(1)
        
    def run(self):
        try:
            # log time for now, as I'm dubious about system time for now
            # (I think when we've had power off we'll be run with the last
            # known time which isn't good - hopefully RTC can be setup to
            # sort local time before anything starts running to avoid this)

            # My doubt about system time is increasing!  RPi runs ntpd with -g option
            # so it should set system time as soon as it gets an ntp peer - so make
            # sure we don't proceed until we've got a valid peer
            first_time = datetime.datetime.now()
            systime_duration = self._pause_for_systime()
            second_time = datetime.datetime.now()
            time_diff = second_time - first_time - datetime.timedelta(seconds=float(systime_duration))
            if time_diff < datetime.timedelta():
                time_diff = "-%s" % (abs(time_diff))
            self.log("*** starting at %s; after %s seconds: %s; difference of %s (HW clock found? %s)" % (first_time, systime_duration, second_time, time_diff, self.hwclock))

            self._think_hard()

            # Remove old files - obviously do this before writing new files
            # in case we need to the space we are going to free up!
            if self.remove_old_files:
                self._remove_old_files()

            # Write our instructional README file to the USB drive
            self._write_readme_file()

            if self.take_photo:
                self._take_photo()

            if self.copy_files:
                self._copy_files()

            # schedule next power on, and power off immediately
            self._schedule_wake_up()

            self.log("shutting down at %s" % (datetime.datetime.now()))
        except Exception as e:
            self.logerr("something went wrong")
            self.logerr(traceback.format_exc())

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print "ERROR: invalid args.  %s {pidfile}" % (sys.argv[0])
        sys.exit(1)
    pidfile = sys.argv[1]
    daemon = TempusFugit(pidfile)
    daemon.start()
