Lpi@box:/opt/iiab/iiab $ cat /etc/usbmount/mount.d/71-usb-library 
#!/bin/bash
# bind mount content from autmounted usb drive into wwwroot
#
# based on a similar script in the xs-rsync package
# by Martin Langhoff <martin@laptop.org>
#
# and the adaptation for xs-activity-server by Douglas Bagnall
# <douglas@paradise.net.nz>
#
# by Jerry Vonau jvonau3@gmail.com

VERBOSE=yes

# bind mount if present
if [ -d $UM_MOUNTPOINT/zims ]; then
    logger -p user.notice -t "71-usb-library" -- "bind mounting $UM_MOUNTPOINT/zims."
    mount --bind $UM_MOUNTPOINT/zims /library/zims
fi
if [ -d $UM_MOUNTPOINT/modules ]; then
    logger -p user.notice -t "71-usb-library" --  "bind mounting $UM_MOUNTPOINT/modules."
    mount --bind $UM_MOUNTPOINT/library/www/html/modules
fi
if [ -d $UM_MOUNTPOINT/maps ]; then
    logger -p user.notice -t "71-usb-library" --  "bind mounting $UM_MOUNTPOINT/maps."
    mount --bind $UM_MOUNTPOINT/maps /library/www/osm-vector-maps
fi
if [ -d $UM_MOUNTPOINT/working ]; then
    logger -p user.notice -t "71-usb-library" --  "bind mounting $UM_MOUNTPOINT/working."
    mount --bind $UM_MOUNTPOINT/working /library/working
fi
if [ -d $UM_MOUNTPOINT/downloads ]; then
    logger -p user.notice -t "71-usb-library" --  "bind mounting $UM_MOUNTPOINT/downloads."
    mount --bind $UM_MOUNTPOINT/working /library/downloads
fi

logger -p user.notice -t "71-usb-library" -- "done"
exit 0
