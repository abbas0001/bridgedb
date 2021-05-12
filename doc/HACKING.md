
# -*- mode: markdown ; coding: utf-8 -*-

HACKING
=======

Hack on whatever you like. Issues are [here][issues]. If you're doing something
big that doesn't have a ticket, you should probably make one. If you don't have
an account you can [request an account][gitlab accounts]

## Generating bridge descriptors

Developers wishing to test BridgeDB will need to generate mock bridge
descriptors. This is accomplished through the file **create-descriptors**.  To
generate 20 bridge descriptors, change to the bridgedb running directory and do:

    $ ./scripts/create-descriptors 20

It is recommended that you generate at least 250 descriptors for testing.
Ideally, even more descriptors should be generated, somewhere in the realm of
2000, as certain bugs do not emerge until BridgeDB is processing thousands of
descriptors.

## Running an email distributor

### Configure postfix as relay

Let's start installing postfix, in debian derivates we can do it using apt:

    $ sudo apt install postfix

Configure postfix to use it in ```/etc/postfix/main.cf```:

    smtpd_relay_restrictions = permit_sasl_authenticated
        permit_mynetworks
        reject_unauth_destination
    relayhost = [smtp.example.com]:587'
    local_recipient_maps =
    # enable SASL authentication
    smtp_sasl_auth_enable = yes
    # disallow methods that allow anonymous authentication.
    smtp_sasl_security_options = noanonymous
    # where to find sasl_passwd
    smtp_sasl_password_maps = hash:/etc/postfix/sasl_passwd
    # Enable STARTTLS encryption
    smtp_use_tls = yes
    # where to find CA certificates
    smtp_tls_CAfile = /etc/ssl/certs/ca-certificates.crt

We'll use postfix to relay all the email over an existing smtp account in an email
provider. Let's add the smtp account into ```/etc/postfix/sasl_passwd```:

    [smtp.example.com]:587 user:password

Set the rights correctly and postmap it so postfix can use it:

    $ sudo chown root:root /etc/postfix/sasl_passwd
    $ sudo chmod 600 /etc/postfix/sasl_passwd
    $ sudo postmap /etc/postfix/sasl_passwd

And restart postfix:

    $ sudo systemctl restart postfix

### Configure bridgedb.conf

    EMAIL_DIST = True
    EMAIL_FROM_ADDR = "user@example.com"
    EMAIL_SMTP_FROM_ADDR = "user@example.com"
    EMAIL_SMTP_HOST = "127.0.0.1"
    EMAIL_SMTP_PORT = 25
    EMAIL_DOMAIN_RULES = {'my.email.provider': ["ignore_dots"]}
    EMAIL_BIND_IP = "127.0.0.1"
    EMAIL_PORT = 6725

### Send bridge request to our local bridgedb

We use swaks to request bridges:

    echo "get transport obfs4" | swaks --to user@example.com --from my.account@my.email.provider --server 127.0.0.1:6725 --body - --header 'Subject: gimme'

And bridgedb will send us an email to ```my.accout@my.email.provider``` using
```smtp.example.com``` as smtp with the bridges.

## Making a release

### Updating dependencies

We maintain three requirements.txt files:

* requirements.txt (for BridgeDB)
* .travis.requirements.txt (for Travis CI)
* .test.requirements.txt (for unit tests)

Each of these files contains pinned dependencies, which are guaranteed to work
for a given release.  Before releasing a new version of BridgeDB, we should
update our dependencies.  The tool [pur][pur] (available through pip) helps us
with this.  It checks a given requirements.txt file and updates each dependency
to its latest version:

    pur -r REQUIREMENTS_FILE

### Bumping the version number

Bumping the version number at release time (which, for BridgeDB really means
deploy time, as of right now) means doing the following:

    $ git checkout develop
    [merge some fix/bug/feature/etc branches]
    $ git checkout -b release-0.0.2 develop
    [bump version number in CHANGELOG]
    [pip maintainance commands *would* go here, if we ever have any]
    $ git checkout master
    $ git merge -S --no-ff release-0.0.2
    $ git tag -a -s bridgedb-0.0.2
    $ git checkout develop
    $ git merge -S --no-ff master
    $ git push <remote> master develop

And be sure not to forget to do:

    $ git push --tags

If the currently installed version is *not* from one of the signed tags, the
version number attribute created by versioneer will be the short ID of the git
commit from which the installation took place, prefixed with the most recent
tagged release at that point, i.e.:

    >>> import bridgedb
    >>> bridgedb.__version__
    0.0.1-git528ff30c

References
----------
[issues]: https://gitlab.torproject.org/tpo/anti-censorship/bridgedb/-/issues
[gitlab accounts]: https://gitlab.onionize.space/
[pur]: https://pypi.org/project/pur/
