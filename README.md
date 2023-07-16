# ShellApi

Simple, basic - definitely not production ready - API web server for any CLI-based process.

## Installation

The only dependencies are Flask and PyYaml. 

```shell
pip install flask==2.3.2 pyyaml==6.0.0
```

## Configuration

Create a YAML file that maps the API endpoints to their shell commands:

```yaml
/play:
    command: winamp play
/pause:
    command: winamp pause
/train:
    command: yolo train image
/detect:
    command: yolo detect image
```

Additionally, you can specify the working directory to run the command:

```yaml
/train:
    command: yolo train image
    working_dir: /workspace/darknet/demo
```

Always insert static arguments before the specified invocation arguments:

```yaml
/detect:
    command: yolo train image
    prepend_args:
        - --model
        - yolov4.weights
        - --config
        - yolov4.cfg
        - --data
        - yolo.data
        - --input
# which yields: yolo train image --model yolov4.weights --config yolov4.cfg --data yolo.data --input $args
```

Or append static arguments after the invocation arguments:

```yaml
/detect:
    command: yolo train image
    append_args:
        - --verbose
```

## Running the API

First, either name your command configuration file "config.yaml" and drop it next to `api.py` or assign the ENV `COMMAND_FILE` the path to it. Then, let 'er rip:

```
python3 api.py
...
```

### Docker Containers

This project was created for the sole purpose of providing a simple, reusable web API to shell-only Docker images. Obviously, some containers may require you to do your due diligence to ensure it still operates expectedly.

Mount `api.py`, `entrypoint.sh` and your command configuration file in the docker container and provide the `COMMAND_FILE` environment variable:

```shell
docker run -v $PWD/api.py:/app/api.py -v $PWD/entrypoint.sh:/app/entrypoint.sh -v $PWD/config.yaml:/app/config.yaml -e COMMAND_FILE=/app/config.yaml ...
```

```docker-compose
    my-app:
        volumes:
            - $PWD:/app
        environment:
            - COMMAND_FILE=/app/config.yaml
```

Then, override the `entrypoint` with `entrypoint.sh` and expose the web server port:

```shell
docker run ... -e PORT=3000 -p 3000:3000 --entrypoint /app/entrypoint.sh ...
```

```docker-compose
    my-app:
        entrypoint: /app/entrypoint.sh
        port:
            - 3000:3000
        environment:
            - PORT=3000
```

That's it! Unless the entrypoint is integral to the application, in which case try the `command`? Good luck!