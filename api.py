"""Quick an dirty web server for any shell command."""
from asyncio import (
    AbstractEventLoop,
    get_event_loop,
    create_subprocess_exec,
    wait_for,
    TimeoutError,
)
import json
import os
import time
from http import HTTPStatus
from typing import List, Tuple, Optional, Dict, Any, OrderedDict, Generator
import subprocess
import logging

import yaml
from flask import jsonify, make_response, request, Flask
from flask.views import MethodView

DEFAULT_TIMEOUT = 3600
COMMAND_FILE = "COMMAND_FILE"

logging.basicConfig(level="DEBUG")
logger = logging.getLogger(__name__)


class CommandOptions:
    """Holds data for registered commands."""

    def __init__(
        self,
        command: str,
        working_dir: Optional[str] = None,
        prepend_args: Optional[List[str]] = None,
        append_args: Optional[List[str]] = None,
    ):
        """Iniitalize a new command options object."""
        self.command = command
        self.working_dir = working_dir
        self.prepend_args: List[str] = prepend_args or []
        self.append_args: List[str] = append_args or []

    @property
    def cwd(self) -> Optional[str]:
        """Get the currentworking directory."""
        return self.working_dir

    def command_args(self, args: List[str] = None) -> List[str]:
        """Merge arguments for command."""
        return self.prepend_args + (args or []) + self.append_args


class CommandRun:
    """Holds the data regarding a command being run."""

    def __init__(self, stdout: str, stderr: str, returncode: int, **kwargs):
        """Initialize command run instance."""
        self.report = stdout
        self.error = stderr
        self.returncode = returncode
        self.start_time = kwargs.get("start_time", None)
        self.end_time = kwargs.get("end_time", None)
        self.process_time = kwargs.get("process_time", None)

    @property
    def was_error(self) -> bool:
        """Whether the run was an error or not."""
        return self.returncode != 0

    @property
    def json(self) -> Optional[Dict[str, Any]]:
        """Get the report as a json object."""
        try:
            return json.loads(self.report)
        except TypeError:
            logger.error("Command report is not a json string.")
            return None


class CommandParser:
    """Utility class to parse command requests."""

    @staticmethod
    def parse_req(
        req, options: CommandOptions
    ) -> Tuple[List[str], Optional[str], int, bool]:
        """Parse the incoming request."""
        args: List[str] = []
        timeout: int = DEFAULT_TIMEOUT
        if not request.is_json:
            raise ValueError("Expected JSON")

        args = req.json.get("args", [])
        timeout = req.json.get("timeout", DEFAULT_TIMEOUT)
        jsonify_stdout = req.json.get("return_json", False)
        args = options.command_args(args)

        cmd: List[str] = options.command.split(" ")
        cmd.extend(args)

        return cmd, options.cwd, timeout, jsonify_stdout

    @staticmethod
    async def run_command(
        cmd: List[str], timeout: int, cwd: Optional[str] = None
    ) -> CommandRun:
        """Runs the given command in a subprocess."""
        start_time: float = time.time()
        proc = await create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout: Optional[str] = None
        stderr: Optional[str] = None
        returncode: int = 0
        try:
            outs, errs = await wait_for(proc.communicate(), timeout=int(timeout))
            stdout = outs.decode("utf-8")
            stderr = errs.decode("utf-8")
            returncode = proc.returncode
            logger.info(f"Job finished with returncode: '{returncode}'.")

        except TimeoutError:
            stdout, _ = [s.decode("utf-8") for s in proc.communicate()]
            stderr = f"command timedout after {timeout} seconds."
            returncode = proc.returncode
            logger.error(f'Job failed: "{stderr}".')

        except Exception as err:
            returncode = -1
            stdout = None
            stderr = str(err)
            logger.error(f'Job failed: "{stderr}".')
        finally:
            if proc.returncode is None:
                proc.terminate()

        end_time: float = time.time()
        process_time = end_time - start_time
        return CommandRun(
            stdout,
            stderr,
            returncode,
            start_time=start_time,
            end_time=end_time,
            process_time=process_time,
        )


class ShellApi:
    """The main application class."""

    __commands: OrderedDict[str, CommandOptions] = OrderedDict()

    def __init__(
        self,
        app=None,
        loop: AbstractEventLoop = None,
        commands: List[CommandOptions] = None,
    ) -> None:
        """Initialize a new shell api instance."""
        if app and commands:
            self.init_app(app, loop or get_event_loop(), commands)

    def init_app(
        self,
        app,
        loop: AbstractEventLoop,
        commands: Dict[str, CommandOptions | Dict[str, str]],
    ) -> None:
        """For use with Flask's `Application Factory`_ method."""
        self.app = app
        self.init_extension()
        self.__loop: AbstractEventLoop = loop

        for endpoint, command_options in commands.items():
            if isinstance(command_options, dict):
                command_options = CommandOptions(
                    command=command_options.get("command"),
                    working_dir=command_options.get("working_dir", None),
                    prepend_args=command_options.get("prepend_args", None),
                    append_args=command_options.get("append_args", None),
                )
            self.register_command(endpoint=endpoint, command_options=command_options)

    def init_extension(self) -> None:
        """Adds the ShellApi() instance to `app.extensions` list."""
        if not hasattr(self.app, "extensions"):
            self.app.extensions = {}

        self.app.extensions["shellapi"] = self

    def register_command(self, endpoint: str, command_options: CommandOptions) -> None:
        """Register a command to a specified endpoint."""
        if self.__commands.get(endpoint):
            logger.error(
                f"Failed to register command, {endpoint} already mapped to {self.__commands.get(endpoint)}"
            )
            return

        logger.info(f"Registering {endpoint} for {command_options}...")
        self.app.add_url_rule(
            endpoint,
            view_func=CommandApiView.as_view(
                endpoint,
                command=command_options,
                loop=self.__loop,
            ),
        )
        self.__commands.update({endpoint: command_options})


class CommandApiView(MethodView):
    """Class to handle the API route."""

    async def post(self):
        """Handle a POST request."""
        try:
            logger.info(
                f"Received request for endpoint: '{request.url_rule}'. "
                f"Requester: '{request.remote_addr}'."
            )
            # Check if request data is correct and parse it
            cmd, cwd, timeout, jsonify_stdout = CommandParser.parse_req(
                request, self.command
            )

            result = await CommandParser.run_command(cmd, cwd=cwd, timeout=timeout)

            if result.returncode != 0:
                return make_response(
                    jsonify(command=cmd, error=result.stderr, report=result.report),
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

            if not jsonify_stdout:
                return make_response(
                    jsonify(command=cmd, report=result.report),
                    HTTPStatus.OK,
                )

            report = json.loads(result.report)
            return make_response(jsonify(**report), HTTPStatus.OK)

        except Exception as err:
            logger.error(err)
            return make_response(jsonify(error=str(err)), HTTPStatus.BAD_REQUEST)

    def __init__(
        self,
        command: CommandOptions,
        loop: AbstractEventLoop,
    ):
        """Initialize a new view."""
        self.command = command
        self.loop = loop


flask_app = Flask(__name__)
command_file = os.environ.get(COMMAND_FILE, "config.yaml")

if not os.path.isfile(command_file):
    raise FileNotFoundError(f"Commands file does not exist at {command_file}")

with open(command_file, "r") as f:
    shell_api = ShellApi(flask_app, get_event_loop(), commands=yaml.safe_load(f))

port = int(os.environ.get("port", 3000))
logger.info(f"Server is listening on port {port}")

flask_app.run(port=port)
