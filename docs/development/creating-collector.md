# Creating a new collector

This page walks you through generating a new collector using Cookiecutter, creating a dedicated virtual environment and running your new collector. The template creates a minimal project and intialises the new directory as a git repository.

The collector is automatically registered as a new CLI command after installing the project dependencies. Running `python main.py collect --help` confirms the project is configured correctly before you start adding logic.


## 1. Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

## 2. Install the OpenHound CLI
```console
uv tool install git+https://github.com/specterops/openhound.git
```

## 3. Create a new collector
Create a new collector using the "create collector" command, which will prompt you for the required details.
```console
openhound create collector
```

Or create the collector by specifying the required options directly

```console
openhound create collector -name MyCollector --service SuperIAM --author MyName --email myname@example.org
```

## 4. Done
You should now be able to see the name of your new collector registered using the following command:
```console
cd <your_collector_directory>
> python main.py collect --help
```

Running the newly created collector will generate 100 dummy assets. Run `python main.py collect <your_service_name> ./output` to test the asset generation process. Next up is writing the actual [collection](collection.md) logic.

PS. Executing the command for the first time may take a few seconds.
