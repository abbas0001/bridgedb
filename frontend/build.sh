#!/usr/bin/env bash

set -eux

export LEKTOR_ENV="$1"

langs=(be ca cs da de es es_AR fr ga he hr hu is it ja ka lt mk ml nb nl pl pt_BR pt_PT ro ru sk sq sv tr zh_CN)

copy_lang() {
    set +u

    lang="$1"
    mkdir -p $(realpath public/"$lang")
    cp $(realpath public_tmp/"$lang"/index.html) $(realpath public/"$lang")
    cp $(realpath public_tmp/"$lang"/captcha/index.html) $(realpath public/"$lang"/captcha.html)
    cp $(realpath public_tmp/"$lang"/bridges/index.html) $(realpath public/"$lang"/bridges.html)
    cp $(realpath public_tmp/"$lang"/options/index.html) $(realpath public/"$lang"/options.html)

    set -u
}

cd "$(dirname "$0")"

lektor build -O public_tmp
rm -rf public
mkdir public

copy_lang ''

for lang in $langs
do
    copy_lang "$lang"
done

cp -r public_tmp/static public/assets

rm -rf public_tmp
