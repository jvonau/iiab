#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

apt update
apt -y upgrade
apt -y install git wget curl

mkdir -p /etc/iiab
# Install local_vars.yml or cp from above
wget https://raw.githubusercontent.com/iiab/iiab/master/vars/local_vars_none.yml -O /etc/iiab/local_vars.yml
#wget https://raw.githubusercontent.com/jvonau/iiab/secure/vars/local_vars_base_secure.yml -O /etc/iiab/local_vars.yml
#wget https://raw.githubusercontent.com/iiab/iiab/master/vars/local_vars_min.yml -O /etc/iiab/local_vars.yml

cat << EOF >> /etc/iiab/local_vars.yml
iiab_admin_install: False
iiab_admin_can_sudo: False
iiab_usb_lib_show_all: False
usb_lib_umask0000_for_kolibri: False
apache_allow_sudo: False
network_install: False
network_enabled: False
frepbx_install: True
frepbx_enabled: True
iiab_hostname: freepbx
iiab_domain: local

EOF

mkdir -p /opt/iiab
cd /opt/iiab
git clone https://github.com/iiab/iiab
cd iiab
git config user.name "iiab-local"
git config user.email "iiab-local@iiab.local"

./iiab-install
