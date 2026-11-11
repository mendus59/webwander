#!/usr/bin/env bash
# Adds ?v=<build> to the site's own scripts, styles and categories file, so a browser
# never mixes a new page with old cached JavaScript after a deploy (GitHub Pages lets
# browsers cache files for 10 minutes).
set -euo pipefail
dir="${1:-site}"
v="${2:-$(git rev-parse --short HEAD)}"
sed -i -E "s#(src|href)=\"((js|css)/[A-Za-z0-9_.-]+\.(js|css))\"#\1=\"\2?v=$v\"#g" "$dir"/*.html
sed -i -E "s#(from |import\()\"(\./[A-Za-z0-9_.-]+\.js)\"#\1\"\2?v=$v\"#g; s#fetch\(\"data/categories\.json\"#fetch(\"data/categories.json?v=$v\"#g" "$dir"/js/*.js
echo "stamped $dir with v=$v"
