#!/bin/bash
cd /home6/daisybuc/public_html/details2enjoy/forms
source bin/activate
exec uwsgi --protocol fastcgi --module forms --callable app 2> error.log
