import datetime
import json
import typing
import urllib.parse
from typing import Type, Optional, Union

import websocket
from dataclasses_json import dataclass_json

from XTBApi.errors import NotLoggedInError, InvalidCall
from XTBApi.models.models import ConnectionMode, ApiCommand, XTBCommand, Symbol, Calendar, CurrentUserData, Trade, RateHistory, \
    RateInfo, Transaction, TransactionStatus
from XTBApi.models.requests import SymbolRequest, TradesRequest, TradesHistoryRequest, ChartLastInfoRecord, ChartLastRequest, ChartRangeRecord, \
    TransactionRequest, TransactionStatusRequest
from XTBApi.xtb_base import XTBBaseClient


class XTBSyncClient(XTBBaseClient):
    def __init__(self, user: str, password: str, mode: ConnectionMode, automatic_logout=True, url: str = "wss://ws.xtb.com/", custom_tag: str = "python-xtb-api", proxy=None):
        super().__init__(user, password, mode, automatic_logout, url, custom_tag)
        self.proxy = proxy

    def _send_message_logged_in(self, command: XTBCommand, payload: Optional[dataclass_json], result_type: Type[dataclass_json]) -> Type[dataclass_json]:
        if not self.logged_in:
            raise NotLoggedInError("Must log in first")

        try:
            return self._send_message(command, payload, result_type)
        except Exception as ex:
            self.logger.exception(f"Error while calling command {command}")
            raise ex  # re-raise for now

    def _send_message(self, command: XTBCommand, payload: Optional[dataclass_json], result_type: Union[Type[dataclass_json], typing.List[dataclass_json]], data_key="returnData"):
        return self._send_raw_message(command, payload, result_type, data_key)

    def _send_raw_message(self, command: XTBCommand, payload: Optional[dataclass_json], result_type: Union[Type[dataclass_json], typing.List[dataclass_json]], data_key):
        # the command we want to send
        self.logger.debug(f"Sending {command} command")  # we don't want to log everything, just the command .. maybe there's some sensitive data involved
        cmd = ApiCommand(command=command, arguments=payload, custom_tag=self.custom_tag)

        raw = cmd.to_json()
        # this will probably not work on on a multi-threaded environment, or where multiple co-routines send and receive data
        # need to investigate this further
        self.xtb_session.send(raw)  # send command
        res = self.xtb_session.recv()  # wait for response
        raw = json.loads(res)
        assert raw["customTag"] == self.custom_tag, f"Custom tag doesn't match {self.custom_tag}"

        if raw["status"]:
            return self._parse_response(raw, result_type, data_key)
        else:
            # try both errorDesc and errorDescr
            desc = raw.get("errorDesc", "")
            if not desc:
                desc = raw.get("errorDescr", "")
            raise InvalidCall(raw["errorCode"] + ". " + desc)

    def login(self) -> None:
        self.stream_session_id = self._send_message(XTBCommand.LOGIN, self.login_request, str, data_key="streamSessionId")
        self.logged_in = True

    def logout(self) -> None:
        self._send_message(XTBCommand.LOGOUT, None, None)
        self.logged_in = False
        self.stream_session_id = None

    def __enter__(self):
        # if we're told not to use a proxy, actually stop using the proxy ffs
        http_no_proxy = None
        if not self.proxy:
            # if we leave it empty the checks inside websocket and url will assume there's no proxy
            # and will actually attempt to use the environment setting for a proxy
            self.proxy = " "  # set a proxy
            http_no_proxy = urllib.parse.urlparse(self.url).hostname  # but mark the hostname as no proxy

        self.xtb_session = websocket.create_connection(f"{self.url}{self.mode.value}", http_proxy_host=self.proxy, http_no_proxy=http_no_proxy)

        self.logger.debug("Entering async_client context manager")
        if not self.logged_in:
            self.login()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.logger.debug("Exiting async_client context manager")
        if self.logged_in and self.automatic_logout:
            self.logout()
        if self.xtb_session:
            self.xtb_session.close()
            self.xtb_session = None
        return self

    def get_all_symbols(self) -> list[Symbol]:
        return self._send_message_logged_in(XTBCommand.GET_ALL_SYMBOLS, None, list[Symbol])

    def get_calendar(self) -> list[Calendar]:
        return self._send_message_logged_in(XTBCommand.GET_CALENDAR, None, list[Calendar])

    def get_current_user_data(self) -> CurrentUserData:
        return self._send_message_logged_in(XTBCommand.GET_CURRENT_USER_DATA, None, CurrentUserData)

    def get_symbol(self, symbol: str) -> Symbol:
        return self._send_message_logged_in(XTBCommand.GET_SYMBOL, SymbolRequest(symbol), Symbol)

    def get_trades(self, opened_only: bool) -> list[Trade]:
        return self._send_message_logged_in(XTBCommand.GET_TRADES, TradesRequest(opened_only), list[Trade])

    def get_trades_history(self, start: datetime.datetime = datetime.datetime.fromtimestamp(0), end: datetime.datetime = datetime.datetime.fromtimestamp(0)) -> list[Trade]:
        return self._send_message_logged_in(XTBCommand.GET_TRADES_HISTORY, TradesHistoryRequest(start=start, end=end), list[Trade])

    def get_chart_last_request(self, chart_info: ChartLastInfoRecord) -> list[RateInfo]:
        # low, high and open are converted to "correct" values in the return object
        data = self._send_message_logged_in(XTBCommand.GET_CHART_LAST_REQUEST, ChartLastRequest(chart_info), RateHistory)
        return self._process_rates(data.rate_infos, data.digits)

    def get_chart_range_request(self, chart_range: ChartRangeRecord) -> list[RateInfo]:
        # low, high and open are converted to "correct" values in the return object
        data = self._send_message_logged_in(XTBCommand.GET_CHART_RANGE_REQUEST, ChartLastRequest(chart_range), RateHistory)
        return self._process_rates(data.rate_infos, data.digits)

    def trade_transaction(self, transaction: Transaction) -> int:
        return self._send_message_logged_in(XTBCommand.TRADE_TRANSACTION, TransactionRequest(transaction), int)

    def transaction_status(self, transaction_id: int) -> TransactionStatus:
        return self._send_message_logged_in(XTBCommand.TRANSACTION_STATUS, TransactionStatusRequest(transaction_id), TransactionStatus)