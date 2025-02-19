# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

"""
SignalExchangeAPI impl for Facebook/Meta's ThreatExchange Graph API platform.

https://developers.facebook.com/programs/threatexchange
https://developers.facebook.com/docs/threat-exchange/reference/apis/
"""


import typing as t
import time
from dataclasses import dataclass, field
from threatexchange.fb_threatexchange.threat_updates import ThreatUpdateJSON
from threatexchange.fetcher.simple.state import SimpleFetchDelta

from threatexchange.fb_threatexchange.api import ThreatExchangeAPI, _CursoredResponse

from threatexchange.fetcher import fetch_state as state
from threatexchange.fetcher.fetch_api import SignalExchangeAPI
from threatexchange.fetcher.collab_config import (
    CollaborationConfigBase,
    DefaultsForCollabConfigBase,
)
from threatexchange.signal_type.signal_base import SignalType


@dataclass
class FBThreatExchangeCollabConfig(
    CollaborationConfigBase, DefaultsForCollabConfigBase
):
    privacy_group: int
    app_token_override: t.Optional[str] = None


@dataclass
class FBThreatExchangeCheckpoint(state.FetchCheckpointBase):
    """
    State about the progress of a /threat_updates-backed state.

    If a client does not resume tailing the threat_updates endpoint fast enough,
    deletion records will be removed, making it impossible to determine which
    records should be retained without refetching the entire dataset from scratch.
    """

    update_time: int = 0
    last_fetch_time: int = field(default_factory=lambda: int(time.time()))

    def is_stale(self) -> bool:
        """
        The API implementation will retain for 90 days

        https://developers.facebook.com/docs/threat-exchange/reference/apis/threat-updates/
        """
        return time.time() - self.last_fetch_time > 3600 * 24 * 85  # 85 days

    def get_progress_timestamp(self) -> int:
        return self.update_time


@dataclass
class FBThreatExchangeOpinion(state.SignalOpinion):

    REACTION_DESCRIPTOR_ID: t.ClassVar[int] = -1

    descriptor_id: t.Optional[int]


@dataclass
class FBThreatExchangeIndicatorRecord(state.FetchedSignalMetadata):

    opinions: t.List[FBThreatExchangeOpinion]

    def get_as_opinions(  # type: ignore  # Why can't mypy tell this is a subclass?
        self,
    ) -> t.List[FBThreatExchangeOpinion]:
        return self.opinions

    @classmethod
    def from_threatexchange_json(
        cls, te_json: ThreatUpdateJSON
    ) -> t.Optional["FBThreatExchangeIndicatorRecord"]:
        if te_json.should_delete:
            return None

        explicit_opinions = {}
        implicit_opinions = {}

        for td_json in te_json.raw_json["descriptors"]["data"]:
            td_id = int(td_json["id"])
            owner_id = int(td_json["owner"]["id"])
            status = (td_json["status"],)
            # added_on = td_json["added_on"]
            tags = td_json.get("tags", [])
            # This is needed because ThreatExchangeAPI.get_threat_descriptors()
            # does a transform, but other locations do not
            if isinstance(tags, dict):
                tags = sorted(tag["text"] for tag in tags["data"])

            category = state.SignalOpinionCategory.WORTH_INVESTIGATING

            if status == "MALICIOUS":
                category = state.SignalOpinionCategory.TRUE_POSITIVE
            elif status == "NON_MALICIOUS":
                category = state.SignalOpinionCategory.FALSE_POSITIVE

            explicit_opinions[owner_id] = FBThreatExchangeOpinion(
                owner_id, category, tags, td_id
            )

            for reaction in td_json.get("reactions", []):
                rxn = reaction["key"]
                owner = int(reaction["value"])
                if rxn == "HELPFUL":
                    implicit_opinions[owner] = state.SignalOpinionCategory.TRUE_POSITIVE
                elif rxn == "DISAGREE_WITH_TAGS" and owner not in implicit_opinions:
                    implicit_opinions[
                        owner
                    ] = state.SignalOpinionCategory.FALSE_POSITIVE

        for owner_id, category in implicit_opinions.items():
            if owner_id in explicit_opinions:
                continue
            explicit_opinions[owner_id] = FBThreatExchangeOpinion(
                owner_id,
                category,
                set(),
                FBThreatExchangeOpinion.REACTION_DESCRIPTOR_ID,
            )

        if not explicit_opinions:
            # Visibility bug of some kind on TE API :(
            return None
        return cls(list(explicit_opinions.values()))

    @staticmethod
    def te_threat_updates_fields() -> t.Tuple[str, ...]:
        """The input to the "field" selector for the API"""
        return (
            "indicator",
            "type",
            "last_updated",
            "should_delete",
            "descriptors{%s}"
            % ",".join(
                (
                    "id",
                    "reactions",
                    "owner{id}",
                    "tags",
                    "status",
                )
            ),
        )


class FBThreatExchangeSignalExchangeAPI(SignalExchangeAPI):
    def __init__(self, fb_app_token: t.Optional[str] = None) -> None:
        self._api = None
        if fb_app_token is not None:
            self._api = ThreatExchangeAPI(fb_app_token)
        self.cursors: t.Dict[str, _CursoredResponse] = {}

    @property
    def api(self) -> ThreatExchangeAPI:
        if self._api is None:
            raise Exception("App Developer token not configured.")
        return self._api

    @classmethod
    def get_checkpoint_cls(cls) -> t.Type[state.FetchCheckpointBase]:
        return FBThreatExchangeCheckpoint

    @classmethod
    def get_record_cls(cls) -> t.Type[FBThreatExchangeIndicatorRecord]:
        return FBThreatExchangeIndicatorRecord

    @classmethod
    def get_config_class(cls) -> t.Type[FBThreatExchangeCollabConfig]:
        return FBThreatExchangeCollabConfig

    def resolve_owner(self, id: int) -> str:
        # TODO -This is supported by the API
        raise NotImplementedError

    def get_own_owner_id(  # type: ignore[override]  # fix with generics on base
        self, collab: FBThreatExchangeCollabConfig
    ) -> int:
        return self.api.app_id

    def fetch_once(  # type: ignore  # fix with generics on base
        self,
        supported_signal_types: t.List[t.Type[SignalType]],
        collab: FBThreatExchangeCollabConfig,
        checkpoint: t.Optional[FBThreatExchangeCheckpoint],
    ) -> state.FetchDelta:
        cursor = self.cursors.get(collab.name)
        start_time = None if checkpoint is None else checkpoint.update_time
        if not cursor:
            cursor = self.api.get_threat_updates(
                collab.privacy_group,
                start_time=start_time,
                page_size=500,
                fields=ThreatUpdateJSON.te_threat_updates_fields(),
                decode_fn=ThreatUpdateJSON,
            )
            self.cursors[collab.name] = cursor

        batch: t.List[ThreatUpdateJSON] = []
        highest_time = 0
        for update in cursor.next():
            # TODO catch errors here
            batch.append(update)
            # Is supposed to be strictly increasing
            highest_time = max(update.time, highest_time)

        # TODO - correctly check types
        return SimpleFetchDelta(
            {
                (
                    u.threat_type,
                    u.indicator,
                ): FBThreatExchangeIndicatorRecord.from_threatexchange_json(
                    u
                )  # type: ignore  # TODO, this is a real type error, but functional for now
                for u in batch
            },
            FBThreatExchangeCheckpoint(highest_time),
            done=cursor.done,
        )

    def report_seen(  # type: ignore[override]  # fix with generics on base
        self,
        collab: FBThreatExchangeCollabConfig,
        s_type: SignalType,
        signal: str,
        metadata: state.FetchedStateStoreBase,
    ) -> None:
        # TODO - this is supported by the API
        raise NotImplementedError

    def report_opinion(  # type: ignore[override]  # fix with generics on base
        self,
        collab: FBThreatExchangeCollabConfig,
        s_type: t.Type[SignalType],
        signal: str,
        opinion: state.SignalOpinion,
    ) -> None:
        # TODO - this is supported by the API
        raise NotImplementedError

    def report_true_positive(  # type: ignore[override]  # fix with generics on base
        self,
        collab: FBThreatExchangeCollabConfig,
        s_type: t.Type[SignalType],
        signal: str,
        metadata: state.FetchedSignalMetadata,
    ) -> None:
        # TODO - this is supported by the API
        self.report_opinion(
            collab,
            s_type,
            signal,
            state.SignalOpinion(
                owner=self.get_own_owner_id(collab),
                category=state.SignalOpinionCategory.TRUE_POSITIVE,
                tags=set(),
            ),
        )

    def report_false_positive(  # type: ignore[override]  # fix with generics on base
        self,
        collab: FBThreatExchangeCollabConfig,
        s_type: t.Type[SignalType],
        signal: str,
        _metadata: state.FetchedSignalMetadata,
    ) -> None:
        self.report_opinion(
            collab,
            s_type,
            signal,
            state.SignalOpinion(
                owner=self.get_own_owner_id(collab),
                category=state.SignalOpinionCategory.FALSE_POSITIVE,
                tags=set(),
            ),
        )
