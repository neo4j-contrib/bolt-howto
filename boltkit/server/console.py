# Copyright (c) 2002-2016 "Neo Technology,"
# Network Engine for Objects in Lund AB [http://neotechnology.com]
#
# This file is part of Neo4j.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from inspect import getmembers
from logging import getLogger
from shlex import split as shlex_split
from time import sleep
from webbrowser import open as open_browser

import click

from boltkit.addressing import AddressList


log = getLogger("boltkit")


class Neo4jConsole:

    args = None

    def __init__(self, service, read, write):
        self.service = service
        self.read = read
        self.write = write

    def __iter__(self):
        for name, value in getmembers(self):
            if isinstance(value, click.Command):
                yield name

    def __getitem__(self, name):
        try:
            f = getattr(self, name)
        except AttributeError:
            raise click.UsageError('No such command "%s".' % name)
        else:
            if isinstance(f, click.Command):
                return f
            else:
                raise click.UsageError('No such command "%s".' % name)

    def _iter_machines(self, name):
        if not name:
            name = "a"
        for spec in list(self.service.machines):
            if name in (spec.name, spec.fq_name):
                yield self.service.machines[spec]

    def _for_each_machine(self, name, f):
        found = 0
        for machine_obj in self._iter_machines(name):
            f(machine_obj)
            found += 1
        return found

    def run(self):
        while True:
            self.args = shlex_split(self.read(self.service.name))
            self.invoke(*self.args)

    def invoke(self, *args):
        try:
            arg0, args = args[0], list(args[1:])
            f = self[arg0]
            ctx = f.make_context(arg0, args, obj=self)
            return f.invoke(ctx)
        except click.exceptions.Exit:
            pass
        except click.ClickException as e:
            self.write(e.format_message())
        except RuntimeError as e:
            log.error("{}: {}".format(e.__class__.__name__, e.args[0]))

    @click.command()
    @click.argument("machine", required=False)
    @click.pass_obj
    def browser(self, machine):
        """ Start the Neo4j browser.

        A machine name may optionally be passed, which denotes the server to
        which the browser should be tied. If no machine name is given, 'a' is
        assumed.
        """

        def f(m):
            self.write("Opening web browser for machine {!r} at "
                       "«{}»".format(m.spec.fq_name, m.spec.http_uri))
            open_browser(m.spec.http_uri)

        if not self._for_each_machine(machine, f):
            raise RuntimeError("Machine {!r} not found".format(machine))

    @click.command()
    @click.pass_obj
    def env(self):
        """ Show available environment variables.

        Each service exposes several environment variables which contain
        information relevant to that service. These are:

          BOLT_SERVER_ADDR   space-separated string of router addresses
          NEO4J_AUTH         colon-separated user and password

        """
        for key, value in sorted(self.service.env().items()):
            self.write("%s=%r" % (key, value))

    @click.command()
    @click.pass_obj
    def exit(self):
        """ Shutdown all machines and exit the console.
        """
        raise SystemExit()

    @click.command()
    @click.argument("command", required=False)
    @click.pass_obj
    def help(self, command):
        """ Get help on a command or show all available commands.
        """
        if command:
            try:
                f = self[command]
            except KeyError:
                raise RuntimeError('No such command "%s".' % command)
            else:
                ctx = self.help.make_context(command, [], obj=self)
                self.write(f.get_help(ctx))
        else:
            self.write("Commands:")
            command_width = max(map(len, self))
            text_width = 73 - command_width
            template = "  {:<%d}   {}" % command_width
            for arg0 in sorted(self):
                f = self[arg0]
                text = [f.get_short_help_str(limit=text_width)]
                for i, line in enumerate(text):
                    if i == 0:
                        self.write(template.format(arg0, line))
                    else:
                        self.write(template.format("", line))

    @click.command()
    @click.pass_obj
    def ls(self):
        """ Show a detailed list of the available servers.

        Each server is listed by name, along with the following details:

        - Bolt port
        - HTTP port
        - Server mode -- CORE, READ_REPLICA or SINGLE
        - Roles the server can fulfil -- (r)ead or (w)rite
        - Docker container in which the server is running

        """
        self.write("NAME        BOLT PORT   HTTP PORT   "
                   "MODE           ROLES   CONTAINER")
        for spec, machine in self.service.machines.items():
            roles = ""
            if machine in self.service.readers:
                roles += "r"
            if machine in self.service.writers:
                roles += "w"
            self.write("{:<12}{:<12}{:<12}{:<15}{:<8}{}".format(
                spec.fq_name,
                spec.bolt_port,
                spec.http_port,
                spec.config.get("dbms.mode", "SINGLE"),
                roles,
                machine.container.short_id,
            ))

    @click.command()
    @click.argument("machine", required=False)
    @click.pass_obj
    def ping(self, machine):
        """ Ping a server by name to check it is available. If no server name
        is provided, 'a' is used as a default.
        """

        def f(m):
            m.ping(timeout=0)

        if not self._for_each_machine(machine, f):
            raise RuntimeError("Machine {!r} not found".format(machine))

    @click.command()
    @click.pass_obj
    def rt(self):
        """ Fetch an updated routing table and display the contents.

        The routing information is cached so that any subsequent `ls` can show
        role information along with each server.
        """
        self.service.update_routing_info()
        self.write(click.style("Routers: «%s»" % AddressList(
            m.address for m in self.service.routers), fg="green"))
        self.write(click.style("Readers: «%s»" % AddressList(
            m.address for m in self.service.readers), fg="green"))
        self.write(click.style("Writers: «%s»" % AddressList(
            m.address for m in self.service.writers), fg="green"))
        self.write(click.style("(TTL: %rs)" % self.service.ttl, fg="green"))

    @click.command()
    @click.argument("machine", required=False)
    @click.pass_obj
    def logs(self, machine):
        """ Display logs for a named server.

        If no server name is provided, 'a' is used as a default.
        """

        def f(m):
            self.write(m.container.logs())

        if not self._for_each_machine(machine, f):
            raise RuntimeError("Machine {!r} not found".format(machine))

    @click.command()
    @click.argument("time", type=float)
    @click.argument("machine", required=False)
    @click.pass_obj
    def pause(self, time, machine):
        """ Pause a server for a given number of seconds.

        If no server name is provided, 'a' is used as a default.
        """

        def f(m):
            log.info("Pausing machine {!r} for {}s".format(m.spec.fq_name,
                                                           time))
            m.container.pause()
            sleep(time)
            m.container.unpause()
            m.ping(timeout=0)

        if not self._for_each_machine(machine, f):
            raise RuntimeError("Machine {!r} not found".format(machine))


class Neo4jClusterConsole(Neo4jConsole):

    @click.command()
    @click.argument("mode")
    @click.pass_obj
    def add(self, mode):
        """ Add a new server by mode.

        The new server can be added in either "core" or "read-replica" mode.
        The full set of MODE values available are:

        - c, core
        - r, rr, replica, read-replica, read_replica

        """
        if mode in ("c", "core"):
            self.service.add_core()
        elif mode == ("r", "rr", "replica", "read-replica", "read_replica"):
            self.service.add_replica()
        else:
            raise click.UsageError('Invalid value for "MODE", choose from '
                                   '"core" or "read-replica"'.format(mode))

    @click.command()
    @click.argument("machine")
    @click.pass_obj
    def rm(self, machine):
        """ Remove a server by name or role.

        Servers can be identified either by their name (e.g. 'a', 'a.fbe340d')
        or by the role they fulfil (i.e. 'r' or 'w').
        """
        if not self.service.remove(machine):
            raise RuntimeError("Machine {!r} not found".format(machine))
