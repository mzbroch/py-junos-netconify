"""
This file defines the 'netconifyCmdo' class.
Used by the 'netconify' shell utility.
"""
import os, sys, json, re
import argparse, jinja2
from ConfigParser import SafeConfigParser
from getpass import getpass
from lxml import etree

import netconify
import netconify.constants as C

__all__ = ['netconifyCmdo']

QFX_MODE_NODE = 'NODE'
QFX_MODE_SWITCH = 'SWITCH'

class netconifyCmdo(object):

    ### -------------------------------------------------------------------------
    ### CONSTRUCTOR
    ### -------------------------------------------------------------------------

    def __init__(self, **kvargs):
        """
        @@@: TBD
        """

        #
        # private attributes
        #
        self._name = None                   
        self._tty = None                    
        self._skip_logout = False
        self.on_notify = None

        #
        # do stuff in the constructor
        #
        self._init_argsparser()

        #
        # public attributes
        #
        self.facts = None
        self.results = dict(changed=False, failed=False, errmsg=None)
 
    ### -------------------------------------------------------------------------
    ### Command Line Arguments Parser 
    ### -------------------------------------------------------------------------

    def _init_argsparser(self):
        p = argparse.ArgumentParser(add_help=True)
        self._argsparser = p

        ## ------------------------------------------------------------------------
        ## input identifiers
        ## ------------------------------------------------------------------------

        p.add_argument('name', 
            nargs='?', 
            help='name of Junos device')

        p.add_argument('--version', action='version', version=C.version )

        ## ------------------------------------------------------------------------
        ## Explicit controls to select the NOOB conf file, vs. netconify
        ## auto-detecting based on read parameters
        ## ------------------------------------------------------------------------

        g = p.add_argument_group('DEVICE options')

        g.add_argument('-f', '--file', 
            dest='junos_conf_file',
            help="Junos configuration file")

        g.add_argument('--qfx-node', 
            dest='qfx_mode', 
            action='store_const', const=QFX_MODE_NODE,
            help='Set QFX device into "node" mode')

        g.add_argument('--qfx-switch', 
            dest='qfx_mode', 
            action='store_const', const=QFX_MODE_SWITCH,
            help='Set QFX device into "switch" mode')

        g.add_argument('--zeroize', 
            dest='request_zeroize',
            action='store_true',
            help='ZEROIZE the device')

        g.add_argument('--shutdown', 
            dest='request_shutdown',
            choices=['poweroff','reboot'],
            help='SHUTDOWN or REBOOT the device')

        g.add_argument('--facts', 
            action='store_true',
            dest='gather_facts',
            help='Gather facts and save them into SAVEDIR')

        ## ------------------------------------------------------------------------
        ## directories
        ## ------------------------------------------------------------------------

        g = p.add_argument_group('DIRECTORY options')

        g.add_argument('-S','--savedir', 
            nargs='?', default='.', 
            help="Files are saved into this directory, $CWD by default")

        g.add_argument('--no-save', 
            action='store_false',
            help="Do not save facts and inventory files")

        ## ------------------------------------------------------------------------
        ## console port 
        ## ------------------------------------------------------------------------

        g = p.add_argument_group('CONSOLE options')

        g.add_argument('-p','--port', 
            default='/dev/ttyUSB0',
            help="serial port device")

        g.add_argument('-b','--baud', 
            default='9600',
            help="serial port baud rate")

        g.add_argument('-t', '--telnet',
            help='terminal server, <host>,<port>')

        g.add_argument('--timeout', 
            default='0.5',
            help='TTY connection timeout (s)')

        ## ------------------------------------------------------------------------
        ## login configuration
        ## ------------------------------------------------------------------------

        g = p.add_argument_group("LOGIN options")

        g.add_argument('-u','--user', 
            default='root',
            help='login user name, defaults to "root"')

        g.add_argument('-P','--passwd', 
            default='',
            help='login user password, *empty* for NOOB')

        g.add_argument('-k', 
            action='store_true', default=False,
            dest='passwd_prompt', 
            help='prompt for user password')

        g.add_argument('-a','--attempts', 
            default=10,
            help='login attempts before giving up')

    ### -------------------------------------------------------------------------
    ### run command, can be involved from SHELL or programmatically
    ### -------------------------------------------------------------------------

    def run(self, args=None):

        # ------------------------        
        # parse command arguments
        # ------------------------

        try:
            # parse command arguments
            self._args = self._argsparser.parse_args(args)
            self._name = self._args.name
        except Exception as err:
            self._hook_exception('parse_args', err)

        args = self._args # alias

        # ----------------------------------
        # handle password input if necessary
        # ----------------------------------

        if args.passwd_prompt is True: args.passwd = getpass()

        # ---------------------------------------------------------------
        # validate command options before going through the LOGIN process
        # ---------------------------------------------------------------

        fname = args.junos_conf_file
        if fname is not None:
            if os.path.isfile(fname) is False:
                self.results['failed'] = True
                self.results['errmsg'] = 'ERROR: unknown file: {}'.format(fname)
                return self.results

        # --------------------
        # login to the CONSOLE
        # --------------------        

        try:
            self._tty_login()      
        except Exception as err:
            self._hook_exception('login', err)

        # ----------------------------------------------------
        # now deal with the various actions/options provided
        # by the command args
        # -----------------------------------------------------

        self._do_actions()

        # ----------------------------------------------------
        # logout, unless we don't need to (due to reboot,etc.)
        # -----------------------------------------------------

        if self._skip_logout is False:
            try:
                self._tty_logout()      
            except Exception as err:
                self._hook_exception('logout', err)

        return self.results

    ### -------------------------------------------------------------------------
    ### Handlers
    ### -------------------------------------------------------------------------

    def _hook_exception(self, event, err):
        sys.stderr.write("ERROR: {}\n".format(err.message))
        sys.exit(1)    

    def _tty_notifier(tty, event, message):
        print "TTY:{}:{}".format(event,message)

    def _notify(self, event, message):
        if self.on_notify is not None:
            self.on_notify(event,message)
        elif self.on_notify is not False:
            print "CMD:{}:{}".format(event,message)

    ### -------------------------------------------------------------------------
    ### LOGIN/LOGOUT
    ### -------------------------------------------------------------------------

    def _tty_login(self):

        tty_args = {}
        tty_args['user'] = self._args.user 
        tty_args['passwd'] = self._args.passwd 
        tty_args['timeout'] = float(self._args.timeout)
        tty_args['attempts'] = int(self._args.attempts)

        if self._args.telnet is not None:
            host,port = re.split('[,:]',self._args.telnet)
            tty_args['host'] = host
            tty_args['port'] = port
            self.console = ('telnet',host,port)
            self._tty = netconify.Telnet(**tty_args)
        else:
            tty_args['port'] = self._args.port      
            tty_args['baud'] = self._args.baud
            self.console = ('serial',self._args.port)
            self._tty = netconify.Serial(**tty_args)

        notify = self.on_notify or self._tty_notifier
        self._tty.login( notify=notify )

    def _tty_logout(self):
        self._tty.logout()    


    ### -------------------------------------------------------------------------
    ### ACTIONS
    ### -------------------------------------------------------------------------

    def _do_actions(self):
        args = self._args # alias

        if args.request_shutdown:
            self._shutdown()
            return

        if args.request_zeroize: 
            self._zeroize()
            return

        if args.gather_facts is True: self._gather_facts()
        if args.junos_conf_file is not None: self._push_config()

    def _zeroize(self):
        """ perform device ZEROIZE actions """
        self._notify('zeroize','ZEROIZE device, rebooting')
        self._tty.nc.zeroize()
        self._skip_logout = True
        self.results['changed'] = True

    def _shutdown(self):
        """ @@@: shutdown or reboot """
        self._skip_logout = True        
        self._notify('shutdown','shutdown {}'.format(self._args.request_shutdown))        
        self._tty.nc.shutdown(reboot=False)
        self._skip_logout = True
        self.results['changed'] = True

    def _gather_facts(self):
        self._notify('facts','retrieving device facts...')    
        self._tty.nc.facts.gather()
        self.facts = self._tty.nc.facts.items

        def my_name():
            """ 
            use the explicit name, the configured host-name, or derive
            the name from the console port
            """
            return self._name or self.facts['hostname'] or '_'.join(self.console)

        def save_facts():
            name = my_name()
            #  save basic facts as JSON file
            fname = name+'-facts.json'
            path = os.path.join(self._args.savedir, fname)
            self._notify('facts','saving: {}'.format(path))
            as_json = json.dumps(self._tty.nc.facts.items)
            with open(path,'w+') as f: f.write(as_json)

            if hasattr(self._tty.nc.facts,'inventory'):
                # also save the inventory as XML file
                fname = name+'-inventory.xml'
                path = os.path.join(self._args.savedir, fname)
                self._notify('inventory','saving: {}'.format(path))
                as_xml = etree.tostring(self._tty.nc.facts.inventory, pretty_print=True)
                with open(path,'w+') as f: f.write(as_xml)

        if self._args.no_save is not False: save_facts()

    def _push_config(self):
        """ push the configuration or rollback changes on error """

        self._notify('conf','loading into device ...')
        content = open(self._args.junos_conf_file,'r').read()
        rc = self._tty.nc.load(content=content)
        if rc is not True:
            self.results['failed'] = True
            self.results['errmsg'] = 'failure to load configuration, aborting.'
            self._notify('conf_ld_err', self.results['errmsg'])
            self._tty.nc.rollback();
            return

        self._notify('conf','commit ... please be patient')
        rc = self._tty.nc.commit()
        if rc is not True:
            self.results['failed'] = True
            self.results['errmsg'] = 'faiure to commit configuration, aborting.'
            self._notify('conf_save_err', self.results['errmsg'])
            self._tty.nc.rollback()
            return

        self._notify('conf','commit completed.')
        self.results['changed'] = True
        return


    ##### !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    ### -------------------------------------------------------------------------
    ### QFX MODE processing
    ### -------------------------------------------------------------------------
    ##### !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!  

    def _qfx_mode(self):
        need_change = False

        # ----------------------------------------------------
        # we need the facts, so if the caller didn't explicity
        # request them, grab them now
        # ----------------------------------------------------

        if self.facts is None: self._gather_facts()
        facts = self.facts # alias

        # --------------------------------------------------------
        # make sure we're logged into a QFX node device.
        # set this up as a list check in case we have other models
        # in the future to deal with.
        # --------------------------------------------------------

        if not any([facts['model'].startswith(m) for m in QFX_MODEL_LIST]):
            self.results['errmsg'] = "Not on a QFX device [{}]".format(facts['model'])
            self.results['failed'] = True
            self._notify('qfx', self.results['errmsg'])
            return

        # --------------------------------------------------------
        # we want to revert the facts information from the 'FPC 0'
        # inventory, rather than the chassis 
        # --------------------------------------------------------

        inv = self._tty.nc.facts.inventory    
        fpc0 = inv.xpath('chassis/chassis-module[name="FPC 0"]')[0]
        facts['serialnumber'] = fpc0.findtext('serial-number')
        facts['model'] = fpc0.findtext('model-number')

        now,later = self._qfx_device_mode_get()

        change = bool(later != self._args.qfx_mode)     # compare to after-reoobt
        reboot = bool(now != self._args.qfx_mode)       # compare to now

        self._notify('info',"QFX mode now/later: {}/{}".format(now, later))
        if now == later and later == self._args.qfx_mode:
            # nothing to do
            self._notify('info','No change required')
        else:
            self._notify('info','Action required')
            need_change = True

        if change is True:
            self._notify('change','Changing the mode to: {}'.format(self._args.qfx_mode))
            self.results['changed'] = True
            self._qfx_device_mode_set()

        if reboot is True:
            self._notify('change','REBOOTING device now!')
            self.results['changed'] = True      
            self._tty.nc.reboot()
            # no need to close the tty, since the device is rebooting ...
            self._skip_logout = True

    ##### !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
    ### -------------------------------------------------------------------------
    ### MISC device RPC commands & controls
    ### -------------------------------------------------------------------------
    ##### !!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

    _QFX_MODES = {
        'Standalone': QFX_MODE_SWITCH,
        'Node-device': QFX_MODE_NODE
    }

    _QFX_XML_MODES = {
        QFX_MODE_SWITCH:'standalone', 
        QFX_MODE_NODE:'node-device'
    }

    def _qfx_device_mode_get(self):
        """ get the current device mode """
        rpc = self._tty.nc.rpc
        got = rpc('show-chassis-device-mode')
        now = got.findtext('device-mode-current')
        later = got.findtext('device-mode-after-reboot')
        return (self._QFX_MODES[now], self._QFX_MODES[later])

    def _qfx_device_mode_set(self):
        """ sets the device mode """
        rpc = self._tty.nc.rpc
        mode = self._QFX_XML_MODES[self._args.qfx_mode]
        cmd = '<request-chassis-device-mode><{}/></request-chassis-device-mode>'.format(mode)
        got = rpc(cmd)
        return True

