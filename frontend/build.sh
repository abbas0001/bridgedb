#!/usr/bin/env bash

set -eux

export LEKTOR_ENV=$1

cd "$(dirname "$0")"

lektor build -O public_tmp
rm -rf public
mkdir public
cp public_tmp/index.html public
cp public_tmp/captcha/index.html public/captcha.html
cp public_tmp/bridges/index.html public/bridges.html
cp public_tmp/options/index.html public/options.html
cp -r public_tmp/static public/assets

rm -rf public_tmp
