"""Quick an dirty web server for any shell command."""
from asyncio import AbstractEventLoop, get_event_loop, create_subprocess_exec, wait_for
import asyncio
import json
import os
import time
import base64
from http import HTTPStatus
from typing import List, Tuple, Optional, Dict, Any, OrderedDict
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
        command: str = "echo",
        working_dir: Optional[str] = None,
        prepend_args: Optional[List[str]] = None,
        append_args: Optional[List[str]] = None,
        static: Optional[bool] = False,
        capture_output: Optional[bool] = True,
        file_names: Optional[List[str]] = None,
    ):
        """Iniitalize a new command options object."""
        self.command = command
        self.working_dir = working_dir
        self.prepend_args: List[str] = prepend_args or []
        self.append_args: List[str] = append_args or []
        self.is_static: bool = static
        self.capture_output = capture_output
        self.file_names = file_names
        self._dict = {
            "command": command,
            "working_dir": working_dir,
            "prepend_args": prepend_args,
            "append_args": append_args,
            "static": static,
            "capture_output": capture_output,
            "file_name": file_name,
        }

    @property
    def is_file_write(self) -> bool:
        """Get whether this is a file write command."""
        return self.file_names is not None

    @property
    def cwd(self) -> Optional[str]:
        """Get the currentworking directory."""
        return self.working_dir

    def command_args(self, args: List[str] = None, decode64: bool = None) -> List[str]:
        """Merge arguments for command."""
        if self.is_file_write:
            arg_i = 0
            for file_name in self.file_names:
                file_path = os.path.join(self.working_dir or "", file_name)
                with open(file_path, "wb") as f:
                    content = args.pop(arg_i)
                    if decode64:
                        content = base64.b64decode(content)
                    f.write(content)
                arg_i += 1

        if self.is_static:
            logger.info(f"{self.command} is marked static, ignoring args")
            return self.prepend_args + self.append_args
        return self.prepend_args + (args or []) + self.append_args

    def __str__(self) -> str:
        """Get string representation."""
        return json.dumps(self._dict)


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
        decode64 = req.json.get("decode64", False)
        jsonify_stdout = req.json.get("return_json", False)
        args = options.command_args(args, decode64)

        logger.info(f"Command Options: {options}")

        cmd: List[str] = options.command.split(" ")
        cmd.extend(args)

        return cmd, options.cwd, timeout, jsonify_stdout

    @staticmethod
    async def run_command(
        cmd: List[str], timeout: int, cwd: Optional[str] = None
    ) -> CommandRun:
        """Runs the given command in a subprocess."""
        start_time: float = time.time()
        logger.info(f"Running command: {cmd[0]} with {len(cmd[1:])} args")
        proc = await create_subprocess_exec(
            cmd[0],
            *cmd[1:],
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

        except asyncio.TimeoutError:
            proc.terminate()
            stdout, _ = [s.decode("utf-8") for s in proc.communicate()]
            stderr = f"command timedout after {timeout} seconds."
            returncode = proc.returncode
            logger.error(f'Job failed: "{stderr}".')

        except Exception as err:
            proc.terminate()
            returncode = -1
            stdout = None
            stderr = str(err)
            logger.error(f'Job failed: "{stderr}".')

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

    __commands: OrderedDict[str, List[CommandOptions]] = OrderedDict()

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
        commands,
    ) -> None:
        """For use with Flask's `Application Factory`_ method."""
        self.app = app
        self.init_extension()
        self.__loop: AbstractEventLoop = loop

        def _dict_to_class(command_options) -> CommandOptions:
            """Convert a dictionary to a class instance."""
            if isinstance(command_options, dict):
                return CommandOptions(**command_options)
            return command_options

        def _process_options(command_options) -> List[CommandOptions]:
            """Process options from configuration."""
            if isinstance(command_options, list):
                return [_dict_to_class(options) for options in command_options]
            return [_dict_to_class(command_options)]

        for endpoint, command_options in commands.items():
            commands = _process_options(command_options)
            self.register_command(endpoint=endpoint, commands=commands)

    def init_extension(self) -> None:
        """Adds the ShellApi() instance to `app.extensions` list."""
        if not hasattr(self.app, "extensions"):
            self.app.extensions = {}

        self.app.extensions["shellapi"] = self

    def register_command(self, endpoint: str, commands: List[CommandOptions]) -> None:
        """Register a command to a specified endpoint."""
        if self.__commands.get(endpoint):
            logger.error(f"Failed to register command, {endpoint} already mapped.")
            return

        logger.info(f"Registering {endpoint} for {len(commands)}...")
        self.app.add_url_rule(
            endpoint,
            view_func=CommandApiView.as_view(
                endpoint,
                commands=commands,
                loop=self.__loop,
            ),
        )
        self.__commands.update({endpoint: commands})


class CommandApiView(MethodView):
    """Class to handle the API route."""

    async def post(self):
        """Handle a POST request."""
        try:
            logger.info(
                f"Received request for endpoint: '{request.url_rule}'. "
                f"Requester: '{request.remote_addr}'."
            )
            report: str = ""
            error: str = ""
            logger.info(
                json.dumps([str(command) for command in self.commands], indent=2)
            )
            command_list = self.commands
            for command in command_list:
                # Check if request data is correct and parse it
                logger.info(str(command))
                cmd, cwd, timeout, jsonify_stdout = CommandParser.parse_req(
                    request, command
                )
                if command.is_file_write:
                    continue
                result = await CommandParser.run_command(cmd, cwd=cwd, timeout=timeout)
                if result.returncode != 0:
                    return make_response(
                        jsonify(
                            command=cmd,
                            error=result.error,
                            report=result.report,
                            previous=report,
                        ),
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                if command.capture_output:
                    logger.info("Capturing output")
                    report = f"{report}\n{result.report}"
                error = f"{error}\n{result.error}"

            if not jsonify_stdout:
                return make_response(
                    jsonify(report=result.report),
                    HTTPStatus.OK,
                )

            report = json.loads(result.report)
            return make_response(jsonify(report=report), HTTPStatus.OK)

        except Exception as err:
            logger.error(err)
            return make_response(jsonify(error=str(err)), HTTPStatus.BAD_REQUEST)

    def __init__(
        self,
        commands: List[CommandOptions],
        loop: AbstractEventLoop,
    ):
        """Initialize a new view."""
        self.commands = commands
        self.loop = loop


flask_app = Flask(__name__)
command_file = os.environ.get(COMMAND_FILE, "config.yaml")

if not os.path.isfile(command_file):
    raise FileNotFoundError(f"Commands file does not exist at {command_file}")

with open(command_file, "r", encoding="ascii") as f:
    shell_api = ShellApi(flask_app, get_event_loop(), commands=yaml.safe_load(f))

port = int(os.environ.get("PORT", 3000))
logger.info(f"Server is listening on port {port}")

flask_app.run(host="0.0.0.0", port=port)
