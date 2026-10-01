"""Temporary per-image outbound blocks using Windows Filtering Platform.

Unlike Windows Firewall policy rules, WFP application IDs accept executable
images with suffixes such as Inno Setup's .tmp. All filters belong to a dynamic
session and are removed by Windows when the engine handle closes.
"""

import ctypes
import uuid

FWPM_SESSION_FLAG_DYNAMIC = 1
FWP_BYTE_BLOB_TYPE = 12
FWP_ACTION_BLOCK = 0x1001


# Fixed-width fields preserve the Windows ABI even in portable layout tests.
class Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_uint8 * 8),
    ]

    @classmethod
    def parse(cls, value):
        return cls.from_buffer_copy(uuid.UUID(str(value)).bytes_le)


ALE_AUTH_CONNECT_LAYERS = (
    Guid.parse("c38d57d1-05a7-4c33-904f-7fbceee60e82"),  # IPv4
    Guid.parse("4a72393b-319f-44bc-84c3-ba54dcb3b6b4"),  # IPv6
)
ALE_APP_ID = Guid.parse("d78e1e87-8644-4ea5-9437-d809ecefc971")


class DisplayData(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("description", ctypes.c_wchar_p)]


class ByteBlob(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("data", ctypes.POINTER(ctypes.c_uint8)),
    ]


class ValueData(ctypes.Union):
    # Unused FWP_VALUE0 union members are also scalars or pointers and have the
    # same size/alignment as these members (including FWP_CONDITION_VALUE0).
    _fields_ = (("uint32", ctypes.c_uint32), ("byte_blob", ctypes.POINTER(ByteBlob)))


class Value(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", ctypes.c_uint32), ("data", ValueData)]


class Session(ctypes.Structure):
    _fields_ = [
        ("key", Guid),
        ("display", DisplayData),
        ("flags", ctypes.c_uint32),
        ("transaction_timeout", ctypes.c_uint32),
        ("process_id", ctypes.c_uint32),
        ("sid", ctypes.c_void_p),
        ("username", ctypes.c_wchar_p),
        ("kernel_mode", ctypes.c_int32),
    ]


class SubLayer(ctypes.Structure):
    _fields_ = [
        ("key", Guid),
        ("display", DisplayData),
        ("flags", ctypes.c_uint16),
        ("provider_key", ctypes.POINTER(Guid)),
        ("provider_data", ByteBlob),
        ("weight", ctypes.c_uint16),
    ]


class FilterCondition(ctypes.Structure):
    _fields_ = [
        ("field_key", Guid),
        ("match_type", ctypes.c_uint32),
        ("value", Value),
    ]


class Action(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("key", Guid)]


class FilterContext(ctypes.Union):
    _fields_ = (("raw", ctypes.c_uint64), ("provider_key", Guid))


class Filter(ctypes.Structure):
    _fields_ = [
        ("key", Guid),
        ("display", DisplayData),
        ("flags", ctypes.c_uint32),
        ("provider_key", ctypes.POINTER(Guid)),
        ("provider_data", ByteBlob),
        ("layer_key", Guid),
        ("sublayer_key", Guid),
        ("weight", Value),
        ("condition_count", ctypes.c_uint32),
        ("conditions", ctypes.POINTER(FilterCondition)),
        ("action", Action),
        ("context", FilterContext),
        ("reserved", ctypes.POINTER(Guid)),
        ("id", ctypes.c_uint64),
        ("effective_weight", Value),
    ]


def _check(error, operation):
    # WFP returns a status code directly; GetLastError is not its error source.
    if error:
        raise OSError(
            f"{operation} failed (0x{error:08X}): {ctypes.FormatError(error).strip()}"
        )


class WfpBlocker:
    def __init__(self):
        self.api = ctypes.WinDLL("fwpuclnt")
        signatures = {
            "FwpmEngineOpen0": [
                ctypes.c_wchar_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(Session),
                ctypes.POINTER(ctypes.c_void_p),
            ],
            "FwpmEngineClose0": [ctypes.c_void_p],
            "FwpmSubLayerAdd0": [
                ctypes.c_void_p,
                ctypes.POINTER(SubLayer),
                ctypes.c_void_p,
            ],
            "FwpmGetAppIdFromFileName0": [
                ctypes.c_wchar_p,
                ctypes.POINTER(ctypes.POINTER(ByteBlob)),
            ],
            "FwpmFilterAdd0": [
                ctypes.c_void_p,
                ctypes.POINTER(Filter),
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint64),
            ],
            "FwpmFreeMemory0": [ctypes.POINTER(ctypes.c_void_p)],
        }
        for name, arguments in signatures.items():
            function = getattr(self.api, name)
            function.argtypes = arguments
            function.restype = None if name == "FwpmFreeMemory0" else ctypes.c_uint32
        self.engine = ctypes.c_void_p()
        self.sublayer = Guid.parse(uuid.uuid4())
        self.blocked = set()

    def __enter__(self):
        session = Session()
        session.flags = FWPM_SESSION_FLAG_DYNAMIC
        session.display.name = "OpenHound Offline Smoke"
        _check(
            self.api.FwpmEngineOpen0(
                None, 10, None, ctypes.byref(session), ctypes.byref(self.engine)
            ),  # RPC_C_AUTHN_WINNT
            "FwpmEngineOpen0",
        )
        sublayer = SubLayer()
        sublayer.key = self.sublayer
        sublayer.display.name = session.display.name
        sublayer.weight = 65535
        try:
            _check(
                self.api.FwpmSubLayerAdd0(self.engine, ctypes.byref(sublayer), None),
                "FwpmSubLayerAdd0",
            )
        except BaseException as error:
            self.__exit__(type(error), error, error.__traceback__)
            raise
        return self

    def block(self, image):
        if image.casefold() in self.blocked:
            return
        app_id = ctypes.POINTER(ByteBlob)()
        _check(
            self.api.FwpmGetAppIdFromFileName0(image, ctypes.byref(app_id)),
            f"FwpmGetAppIdFromFileName0({image})",
        )
        try:
            condition = FilterCondition()
            condition.field_key = ALE_APP_ID
            condition.match_type = 0  # FWP_MATCH_EQUAL
            condition.value.type = FWP_BYTE_BLOB_TYPE
            condition.value.byte_blob = app_id
            for layer in ALE_AUTH_CONNECT_LAYERS:
                filter = Filter()
                filter.key = Guid.parse(uuid.uuid4())
                filter.display.name = "OpenHound Offline Smoke"
                filter.layer_key = layer
                filter.sublayer_key = self.sublayer
                filter.condition_count = 1
                filter.conditions = ctypes.pointer(condition)
                filter.action.type = FWP_ACTION_BLOCK
                # FWP_EMPTY weight lets WFP assign the filter's weight.
                _check(
                    self.api.FwpmFilterAdd0(
                        self.engine, ctypes.byref(filter), None, None
                    ),
                    f"FwpmFilterAdd0({image})",
                )
            self.blocked.add(image.casefold())
        finally:
            self.api.FwpmFreeMemory0(
                ctypes.cast(ctypes.byref(app_id), ctypes.POINTER(ctypes.c_void_p))
            )

    def __exit__(self, exception_type, exception, traceback):
        error = self.api.FwpmEngineClose0(self.engine)
        self.engine = ctypes.c_void_p()
        try:
            _check(error, "FwpmEngineClose0")
        except OSError as cleanup_error:
            if exception is None:
                raise
            exception.add_note(str(cleanup_error))
