import fnmatch
import logging
import os
import sh
import time

import pebble as libpebble

from PblCommand import PblCommand
import PblAnalytics

PEBBLE_PHONE_ENVVAR='PEBBLE_PHONE'

class ConfigurationException(Exception):
    pass

class NoCompilerException(Exception):
    """ Returned by PblBuildCommand if we couldn't find the ARM tools """
    pass

class BuildErrorException(Exception):
    """ Returned by PblBuildCommand if there was a compile or link error """
    pass

class AppTooBigException(Exception):
    """ Returned by PblBuildCommand if the app is too big"""
    pass


class LibPebbleCommand(PblCommand):

    def configure_subparser(self, parser):
        PblCommand.configure_subparser(self, parser)
        parser.add_argument('--phone', type=str, default=os.getenv(PEBBLE_PHONE_ENVVAR),
                help='The IP address or hostname of your phone - Can also be provided through PEBBLE_PHONE environment variable.')
        parser.add_argument('--verbose', type=bool, default=False, help='Prints received system logs in addition to APP_LOG')

    def run(self, args):
        if not args.phone:
            raise ConfigurationException("Argument --phone is required (Or set a PEBBLE_PHONE environment variable)")
        self.pebble = libpebble.Pebble()
        self.pebble.set_print_pbl_logs(args.verbose)
        self.pebble.connect_via_websocket(args.phone)

    def tail(self, interactive=False, skip_enable_app_log=False):
        if not skip_enable_app_log:
            self.pebble.app_log_enable()
        if interactive:
            logging.info('Entering interactive mode ... Ctrl-D to interrupt.')
            def start_repl(pebble):
                import code
                import readline
                import rlcompleter

                readline.set_completer(rlcompleter.Completer(locals()).complete)
                readline.parse_and_bind('tab:complete')
                code.interact(local=locals())
            start_repl(self.pebble)
        else:
            logging.info('Displaying logs ... Ctrl-C to interrupt.')
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print "\n"
        self.pebble.app_log_disable()

class PblPingCommand(LibPebbleCommand):
    name = 'ping'
    help = 'Ping your Pebble project to your watch'

    def configure_subparser(self, parser):
        LibPebbleCommand.configure_subparser(self, parser)

    def run(self, args):
        LibPebbleCommand.run(self, args)
        self.pebble.ping(cookie=0xDEADBEEF)

class PblInstallCommand(LibPebbleCommand):
    name = 'install'
    help = 'Install your Pebble project to your watch'

    def get_pbw_path(self):
        return 'build/{}.pbw'.format(os.path.basename(os.getcwd()))

    def configure_subparser(self, parser):
        LibPebbleCommand.configure_subparser(self, parser)
        parser.add_argument('pbw_path', type=str, nargs='?', default=self.get_pbw_path(), help='Path to the pbw to install (ie: build/*.pbw)')
        parser.add_argument('--logs', action='store_true', help='Display logs after installing the app')

    def run(self, args):
        LibPebbleCommand.run(self, args)

        if not os.path.exists(args.pbw_path):
            logging.error("Could not find pbw <{}> for install.".format(args.pbw_path))
            return 1

        if args.logs:
            self.pebble.app_log_enable()

        success = self.pebble.install_bundle_ws(args.pbw_path)

        # Send the phone OS version to analytics
        phoneInfoStr = self.pebble.get_phone_info()
        PblAnalytics.phone_info_evt(phoneInfoStr = phoneInfoStr)

        if success and args.logs:
            self.tail(skip_enable_app_log=True)

class PblListCommand(LibPebbleCommand):
    name = 'list'
    help = 'List the apps installed on your watch'

    def configure_subparser(self, parser):
        LibPebbleCommand.configure_subparser(self, parser)

    def run(self, args):
        LibPebbleCommand.run(self, args)

        try:
            response = self.pebble.get_appbank_status()
            apps = response['apps']
            if len(apps) == 0:
                logging.info("No apps installed.")
            for app in apps:
                logging.info('[{}] {}'.format(app['index'], app['name']))
        except:
            logging.error("Error getting apps list.")
            return 1

class PblRemoveCommand(LibPebbleCommand):
    name = 'rm'
    help = 'Remove an app from your watch'

    def configure_subparser(self, parser):
        LibPebbleCommand.configure_subparser(self, parser)
        parser.add_argument('bank_id', type=int, help="The bank id of the app to remove (between 1 and 8)")

    def run(self, args):
        LibPebbleCommand.run(self, args)

        for app in self.pebble.get_appbank_status()['apps']:
            if app['index'] == args.bank_id:
                self.pebble.remove_app(app["id"], app["index"])
                logging.info("App removed")
                return 0

        logging.info("No app found in bank %u" % args.bank_id)
        return 1

class PblCurrentAppCommand(LibPebbleCommand):
    name = 'current'
    help = 'Get the uuid and name of the current app'

    def run(self, args):
        LibPebbleCommand.run(self, args)

        uuid = self.pebble.current_running_uuid()
        uuid_hex = uuid.translate(None, '-')
        if not uuid:
            return
        elif int(uuid_hex, 16) == 0:
            print "System"
            return

        print uuid
        d = self.pebble.describe_app_by_uuid(uuid_hex)
        if not isinstance(d, dict):
            return
        print "Name: %s\nCompany: %s\nVersion: %d" % (d.get("name"), d.get("company"), d.get("version"))
        return

class PblListUuidCommand(LibPebbleCommand):
    name = 'uuids'
    help = 'List the uuids and names of installed apps'

    def run(self, args):
        LibPebbleCommand.run(self, args)

        uuids = self.pebble.list_apps_by_uuid()
        if len(uuids) is 0:
            logging.info("No apps installed.")

        for uuid in uuids:
            uuid_hex = uuid.translate(None, '-')
            description = self.pebble.describe_app_by_uuid(uuid_hex)
            if not description:
                continue

            print '%s - %s' % (description["name"], uuid)

class PblScreenshotCommand(LibPebbleCommand):
    name = 'screenshot'
    help = 'take a screenshot of the pebble'

    def run(self, args):
        LibPebbleCommand.run(self, args)

        logging.info("Taking screenshot...")
        def progress_callback(amount):
            logging.info("%.2f%% done..." % (amount*100.0))

        image = self.pebble.screenshot(progress_callback)
        name = time.strftime("pebble-screenshot_%Y-%m-%d_%H-%M-%S.png")
        try:
            image.save(name, "PNG")
        except TypeError as e:
            # NOTE: Some customers have experienced the following exception
            #  during image.save: "TypeError: function takes at most 4 arguments 
            #   (6 given)". This is due to having the Pillow python modules
            #   call into PIL compiled binaries. This apparently can happen
            #   after an upgrade to MacOS 10.9 or XCode 5 depending on which
            #   versions of PIL and/or Pillow were installed before the upgrade. 
            if "function takes at most" in e.message:
                logging.error("CONFLICT DETECTED: We detected two conflicting "
                  "installations of the same python package for image "
                  "processing (PIL and Pillow) and could not proceed. In order "
                  "to clear up this conflict, please run the following commands "
                  "from a terminal window and try again: "
                  "\n    pip uninstall PIL"
                  "\n    pip install --user Pillow"
                  "\n")
                raise Exception("Conflicting PIL and Pillow packages")
            else:
                raise
        logging.info("Screenshot saved to %s" % name)

        # Open up the image in the user's default image viewer. For some
        # reason, this doesn't seem to open it up in their webbrowser,
        # unlike how it might appear. See
        # http://stackoverflow.com/questions/7715501/pil-image-show-doesnt-work-on-windows-7
        try:
            import webbrowser
            webbrowser.open(name)
        except:
            logging.info("Note: Failed to open image, you'll have to open it "
                         "manually if you want to see what it looks like ("
                         "it has still been saved, however).")


class PblLogsCommand(LibPebbleCommand):
    name = 'logs'
    help = 'Continuously displays logs from the watch'

    def configure_subparser(self, parser):
        LibPebbleCommand.configure_subparser(self, parser)

    def run(self, args):
        LibPebbleCommand.run(self, args)
        self.tail()

class PblReplCommand(LibPebbleCommand):
    name = 'repl'
    help = 'Launch an interactive python shell with a `pebble` object to execute methods on.'

    def run(self, args):
        LibPebbleCommand.run(self, args)
        self.tail(interactive=True)
