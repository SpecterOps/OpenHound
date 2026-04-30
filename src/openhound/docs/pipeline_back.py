import importlib
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import griffe
from jinja2 import Environment, FileSystemLoader

ROOT_PATH = Path(__file__).resolve().parents[1]


@dataclass
class Collector:
    assets: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    resources: list = field(default_factory=list)
    transformers: list = field(default_factory=list)


@dataclass
class DocumentedField:
    name: str
    type_name: str
    description: str | None = None


class GraphResourceDecorator(griffe.Extension):
    def __init__(self) -> None:
        super().__init__()
        self.collectors: dict[str, Collector] = defaultdict(Collector)
        self.griffe_data = None

    @staticmethod
    def _collector(args) -> dict[str, str]:
        """Parses the collector name and description/help as a string.

        Args:
            args: The function's original arguments.

        Returns:
            A dictionary containing the collector name and help description.
        """
        return {
            "name": args[0].replace("'", ""),
            "help": args[1].value.replace("'", ""),
        }

    @staticmethod
    def _description(arg, cls) -> dict[str, str]:
        """Parses the asset description as a string.

        Args:
            arg: The decorator argument for 'description'.
            cls: The griffe Class being processed (unused, kept for uniform signature).

        Returns:
            A dictionary containing the asset description.
        """
        return {"description": arg.value.replace("'", "")}

    @staticmethod
    def _resolve(expr, cls):
        """Resolve a decorator argument to its actual runtime string value"""

        # Plain text string
        if isinstance(expr, str):
            return expr.replace("'", "")

        # ExprAttribute, ie. module constants like nk.USER
        if isinstance(expr, griffe.ExprAttribute):
            alias_name = expr.values[0].name  # the module, ex. "nk"
            attr_name = expr.values[1].name  # the attribute, ex. "USER"

            alias_obj = cls.module.members.get(alias_name)
            if alias_obj is not None and hasattr(alias_obj, "target_path"):
                mod = importlib.import_module(alias_obj.target_path)
                return getattr(mod, attr_name)

        if isinstance(expr, griffe.ExprName):
            return expr.canonical_path.replace("'", "")

        # A fallback
        return str(expr).replace("'", "")

    @staticmethod
    def _parse_node_properties(properties_cls: griffe.Class, docstring_style: Literal['google'] = "google") -> dict:
        """Parses the node properties as a dictionary.

        Args:
            properties_cls: The Griffe class being processed.
        """

        descriptions: dict[str, str] = {}
        all_attributes = {}
        for attribute, content in properties_cls.attributes.items():
            all_attributes[attribute] = {'type': content.annotation.canonical_name}

        sections = properties_cls.docstring.parse(docstring_style)
        for section in sections:
            if section.kind is not griffe.DocstringSectionKind.attributes:
                continue
            for docs_attribute in section.value:
                descriptions[docs_attribute.name] = (docs_attribute.description or "").strip()

        return descriptions

    @staticmethod
    def _node(arg, cls) -> dict:
        """Parse a ``NodeDef(...)`` as a dictionary. Returns the kind, description, icon."""
        node = {}
        for argument in arg.value.arguments:
            if argument.name in ['kind', 'description', 'icon']:
                node[argument.canonical_name] = GraphResourceDecorator._resolve(
                    argument.value, cls
                )
            if argument.name == 'properties':
                node['properties'] = GraphResourceDecorator._parse_node_properties(argument.value.resolved)

        return {"node": node}

    @staticmethod
    def _edges(arg, cls) -> dict:
        """Parse the ``EdgeDef(...)`` edges as a dictionary. Returns the start, end and kind."""

        # TODO: Make these conditional with custom parsers based on the argument
        edges = []
        for elem in arg.value.elements:
            edge = {}
            for a in elem.arguments:
                parsed_value = GraphResourceDecorator._resolve(a.value, cls)
                if a.canonical_name == "traversable":
                    parsed_value = True if parsed_value == "True" else False
                edge[a.canonical_name] = parsed_value
            edges.append(edge)
        return {"edges": edges}

    def on_function(self, *, func: griffe.Function, **kwargs) -> None:
        """Generates an overview of source, resources and transformers.

        Args:
            func (griffe.Function): The griffe function being processed
        """
        collector_decorators = ("app.source", "app.resource", "app.transformer")
        matched = None
        for decorator in func.decorators:
            for suffix in collector_decorators:
                if decorator.callable_path.endswith(suffix):
                    matched = decorator
                    break

        if matched:
            collector_meta = self._collector(func.module.members["app"].value.arguments)
            collector = self.collectors[collector_meta["name"]]
            func_name = func.name
            for arg in matched.value.arguments:
                if arg.canonical_name == "name":
                    func_name = str(arg.value).strip("'\"")
                    break

            entry = {
                "function_name": func.name,
                "module_path": func.path,
                "name": func_name,
            }

            # TODO: This can probably be simplified
            if matched.callable_path.endswith("app.source"):
                collector.sources.append(entry)
            elif matched.callable_path.endswith("app.resource"):
                collector.resources.append(entry)
            elif matched.callable_path.endswith("app.transformer"):
                collector.transformers.append(entry)

    def on_class(
            self,
            *,
            cls: griffe.Class,
            **kwargs,
    ) -> None:

        parsers = {
            "node": lambda a: self._node(a, cls),
            "description": lambda a: self._description(a, cls),
            "edges": lambda a: self._edges(a, cls),
        }

        for decorator in cls.decorators:
            if not decorator.callable_path.endswith("app.asset"):
                continue

            resource_as_dict = {}
            collector_meta = self._collector(cls.module.members["app"].value.arguments)
            collector = self.collectors[collector_meta["name"]]

            for args in decorator.value.arguments:
                parser = parsers.get(args.canonical_name)
                if parser:
                    result = parser(args)
                    resource_as_dict = {
                        **resource_as_dict,
                        **result,
                        "class": cls.name,
                        "path": cls.path,
                    }
            collector.assets.append(resource_as_dict)


class CustomCollectorDocs:
    def __init__(
            self,
            name: str,
            base_docs_dir: Path,
            assets: list[dict],
            sources: list[dict] | None = None,
            resources: list[dict] | None = None,
            transformers: list[dict] | None = None,
            descriptions_dir: Path | None = None,
            template_dir: Path = Path("docs/templates"),
    ):
        self.name = name
        self.base_docs_dir = base_docs_dir
        self.descriptions_dir = descriptions_dir or (base_docs_dir / "descriptions")
        self.assets = assets
        self.sources = sources or []
        self.resources = resources or []
        self.transformers = transformers or []
        self.env = Environment(
            loader=FileSystemLoader(ROOT_PATH / template_dir),
            extensions=["jinja2.ext.do"],
        )
        self.env.globals["escape_markdown_cell"] = self.escape_markdown_cell
        self._griffe_class_cache: dict[str, griffe.Class | None] = {}

    @staticmethod
    def escape_markdown_cell(value: Any) -> str:
        if value is None:
            return ""
        return str(value).replace("|", r"\|").replace("\n", "<br>")

    def _load_griffe_class(self, class_path: str) -> griffe.Class | None:
        if class_path in self._griffe_class_cache:
            return self._griffe_class_cache[class_path]

        try:
            loaded_obj = griffe.load(
                class_path,
                resolve_aliases=True,
                resolve_external=True,
            )
        except ImportError:
            self._griffe_class_cache[class_path] = None
            return None

        resolved_class = loaded_obj if isinstance(loaded_obj, griffe.Class) else None
        self._griffe_class_cache[class_path] = resolved_class

        return resolved_class

    @staticmethod
    def _format_type_name(annotation: Any) -> str:
        if annotation is None or annotation is type(None):
            return "None"

        if isinstance(annotation, str):
            return annotation

        if getattr(annotation, "__module__", "") == "builtins" and hasattr(
                annotation, "__name__"
        ):
            return annotation.__name__

        rendered = str(annotation)
        # TODO: what the fuck is this
        # if rendered.startswith("<class '") and rendered.endswith("'>"):
        #     return rendered.removeprefix("<class '").removesuffix("'>").split(".")[-1]

        return rendered.replace("typing.", "").replace("NoneType", "None")

    def _field_descriptions(
            self, cls: griffe.Class, include_inherited: bool = False
    ) -> dict[str, str]:
        descriptions: dict[str, str] = {}

        # TODO: Slop
        if include_inherited:
            classes = [*reversed(cls.mro()), cls]
        else:
            classes = [cls]

        for mro_cls in classes:
            docstring = mro_cls.docstring
            if docstring is None:
                continue

            sections = docstring.parse("google")
            for section in sections:
                if section.kind is not griffe.DocstringSectionKind.attributes:
                    continue
                for attribute in section.value:
                    descriptions[attribute.name] = (attribute.description or "").strip()

        return descriptions

    def _documented_fields(
            self, cls: griffe.Class, include_inherited: bool = False
    ) -> list[DocumentedField]:
        descriptions = self._field_descriptions(cls, include_inherited=include_inherited)
        members = cls.all_members if include_inherited else cls.members

        documented_fields: list[DocumentedField] = []
        for field_name, member in members.items():
            labels = getattr(member, "labels", set())
            annotation = getattr(member, "annotation", None)

            if "instance-attribute" not in labels:
                continue
            if annotation is None:
                continue

            documented_fields.append(
                DocumentedField(
                    name=field_name,
                    type_name=self._format_type_name(annotation),
                    description=descriptions.get(field_name) or None,
                )
            )

        return documented_fields

    def render_class_table(self, class_path: str, include_inherited: bool = False) -> str:
        template = self.env.get_template("class_table.md.j2")
        resolved_class = self._load_griffe_class(class_path)
        if resolved_class is None:
            return template.render(fields=[])

        # TODO: Handle multiple cases for:
        # dataclasses, pydantic models

        return template.render(
            fields=self._documented_fields(
                resolved_class, include_inherited=include_inherited
            )
        )

    def _description_file_content(self, category: str, name: str) -> str:
        description_path = self.descriptions_dir / self.name / category / f"{name}.md"
        if description_path.is_file():
            return description_path.read_text()
        return ""

    @property
    def collector_template(self) -> str:
        """Renders the collector overview template

        Returns:
            str: The rendered template as a string
        """
        template = self.env.get_template("overview.md.j2")
        result = template.render(name=self.name, graph_resources=self.assets)
        return result

    @property
    def pipeline_template(self) -> str:
        """Renders the DLT source/resource/transformer function inventory.

        Note: Only works when wrapped with the OpenHound @app.source/@resource/@transformer decorators

        Returns:
            str: The rendered template as a string.
        """
        template = self.env.get_template("pipeline.md.j2")
        return template.render(
            name=self.name,
            sources=self.sources,
            resources=self.resources,
            transformers=self.transformers,
        )

    @property
    def asset_templates(self) -> list[dict[str, str]]:
        """Renders the individual assets used by a collector

        Returns:
            list[dict[str, str]]: Returns a list of rendered templates
        """
        results = []
        template = self.env.get_template("asset.md.j2")
        for asset in self.assets:
            graph_resource = {**asset}
            graph_resource["resource_attributes_table"] = self.render_class_table(
                graph_resource["path"]
            )
            if graph_resource.get("node", {}).get("properties"):
                graph_resource["node_properties_table"] = self.render_class_table(
                    graph_resource["node"]["properties"], include_inherited=True
                )

            result = template.render(graph_resource=graph_resource)
            results.append({**graph_resource, "render": result})
        return results

    @property
    def _node_index(self) -> dict[str, dict]:
        """Creates a mapping of unique node kinds with their properties and incoming/outgoing edges

        Returns:
            dict[str, dict]: Node details by node name/id
        """
        all_nodes: dict[str, dict] = {}

        # Iterate over all assets and populate the all_nodes index with all the unique node types
        for asset in self.assets:
            node = asset.get("node")
            if not node or not node.get("kind"):
                continue

            kind = node["kind"]
            if kind not in all_nodes:
                all_nodes[kind] = {
                    "kind": kind,
                    "icon": node["icon"],
                    "properties": node.get("properties"),
                    "color": node.get("color", "#FFFFFF"),
                    "produced_by": asset["class"],
                    "incoming": [],
                    "outgoing": [],
                }

        # TODO: This can probably be done more efficiently compared to iterating over assets twice
        for asset in self.assets:
            for edge in asset.get("edges", []):
                start = edge["start"]
                end = edge["end"]
                if start in all_nodes and edge not in all_nodes[start]["outgoing"]:
                    all_nodes[start]["outgoing"].append(edge)
                if end in all_nodes and edge not in all_nodes[end]["incoming"]:
                    all_nodes[end]["incoming"].append(edge)

        return all_nodes

    @property
    def _edge_index(self) -> dict[str, dict]:
        """Creates a mapping of unique edge kinds with their properties and all node pairs that use them.

        Each entry is enriched with start/end node icons from the node index so
        templates do not need to perform separate lookups.

        Returns:
            dict[str, dict]: Edge details keyed by edge kind.
        """
        all_edges: dict[str, dict] = {}
        node_index = self._node_index

        for asset in self.assets:
            for edge in asset.get("edges", []):
                kind = edge.get("kind")
                if not kind:
                    continue

                if kind not in all_edges:
                    all_edges[kind] = {
                        "kind": kind,
                        "description": edge.get("description", ""),
                        "traversable": edge.get("traversable", False),
                        "instances": [],
                        "produced_by": [],
                    }

                instance = {
                    "start": edge["start"],
                    "end": edge["end"],
                    "start_icon": node_index.get(edge["start"], {}).get(
                        "icon", "circle"
                    ),
                    "end_icon": node_index.get(edge["end"], {}).get("icon", "circle"),
                }
                if instance not in all_edges[kind]["instances"]:
                    all_edges[kind]["instances"].append(instance)

                if asset["class"] not in all_edges[kind]["produced_by"]:
                    all_edges[kind]["produced_by"].append(asset["class"])

        return all_edges

    @property
    def node_templates(self) -> list[dict[str, str]]:
        """Renders a node document for each node in the node index

        Returns:
            list[dict[str, str]]: Each result has contains the node kind and rendered document.
        """
        results = []
        template = self.env.get_template("node.md.j2")
        index = self._node_index
        for kind, node_data in index.items():
            node = {**node_data}
            if node.get("properties"):
                node["properties_table"] = self.render_class_table(
                    node["properties"], include_inherited=True
                )

            render = template.render(
                name=self.name,
                node=node,
                node_index=index,
                node_description=self._description_file_content("nodes", kind),
            )
            results.append({"kind": kind, "render": render})
        return results

    @property
    def edge_templates(self) -> list[dict[str, str]]:
        """Renders an edge document for each unique edge kind in the edge index.

        Returns:
            list[dict[str, str]]: Each entry contains the edge kind and rendered document.
        """
        results = []
        template = self.env.get_template("edge.md.j2")
        for kind, edge_data in self._edge_index.items():
            render = template.render(
                name=self.name,
                edge=edge_data,
                edge_description=self._description_file_content("edges", kind),
            )
            results.append({"kind": kind, "render": render})
        return results

    @staticmethod
    def safe_create(base_dir: Path, target_dir: Path, parents: bool = False) -> None:
        """Safely creates a directory within a base directory

        Args:
            base_dir (Path): The base directory
            target_dir (Path): The target directory to create
            parents (bool, optional): Whether to create parent directories. Defaults to False.

        Raises:
            ValueError: If the target directory is outside the base directory
        """
        if target_dir.resolve().is_relative_to(base_dir.resolve()):
            target_dir.mkdir(parents=parents, exist_ok=True)

        else:
            raise ValueError(f"Detected path traversal attempt for {str(target_dir)}")

    @staticmethod
    def safe_write(base_dir: Path, target_dir: Path, text: str) -> None:
        """Safely writes text to a file within a base directory

        Args:
            base_dir (Path): The base directory
            target_dir (Path): The target file to write
            text (str): The text to write to the file

        Raises:
            ValueError: If the target file is outside the base directory
        """
        if target_dir.resolve().is_relative_to(base_dir.resolve()):
            target_dir.write_text(text)

        else:
            raise ValueError(f"Detected path traversal attempt for {str(target_dir)}")

    def to_markdown(self, output_path: Path):
        collection_path = output_path / "collection"
        graph_path = output_path / "graph"
        self.safe_create(self.base_docs_dir, (collection_path / "assets"), parents=True)
        self.safe_create(self.base_docs_dir, (graph_path / "nodes"), parents=True)
        self.safe_create(self.base_docs_dir, (graph_path / "edges"), parents=True)

        (collection_path / "overview.md").write_text(self.collector_template)
        self.safe_write(
            self.base_docs_dir, collection_path / "pipeline.md", self.pipeline_template
        )

        for asset in self.asset_templates:
            resource_path = collection_path / "assets" / f"{asset['class']}.md"
            self.safe_write(self.base_docs_dir, resource_path, asset["render"])

        for node in self.node_templates:
            node_path = graph_path / "nodes" / f"{node['kind']}.md"
            self.safe_write(self.base_docs_dir, node_path, node["render"])

        for edge in self.edge_templates:
            edge_path = graph_path / "edges" / f"{edge['kind']}.md"
            self.safe_write(self.base_docs_dir, edge_path, edge["render"])
