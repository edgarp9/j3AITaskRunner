"""Manual candidate UI helpers for preset sessions."""

from __future__ import annotations

from collections.abc import Sequence
import tkinter as tk
from tkinter import ttk

from app.runtime import (
    PresetManualCandidateSelectionClearedEvent,
    PresetManualCandidateSelectionContinuedEvent,
    PresetManualCandidateSelectionRequiredEvent,
)
from domain import PresetCandidate

from .i18n import localize_runtime_message
from .main_window_preset_options import MainWindowPresetOptionsMixin
from .main_window_shared import (
    _queue_full_session_view_refresh,
    _tr_for,
    _window_language,
)
from .main_window_state import RuntimeUiUpdateBatch, SessionWidgets


class MainWindowPresetCandidateMixin(MainWindowPresetOptionsMixin):
    def _apply_manual_candidate_selection_required(
        self,
        event: PresetManualCandidateSelectionRequiredEvent,
        updates: RuntimeUiUpdateBatch,
    ) -> None:
        if self._has_session_view(event.parent_session_tab_id):
            self._render_preset_manual_candidates(
                event.parent_session_tab_id,
                event.candidates,
                editable=True,
                status_message=_tr_for(
                    self,
                    "manual_candidates_waiting",
                    count=len(event.candidates),
                ),
            )
            self._select_session_candidates_tab(event.parent_session_tab_id)
        updates.status_message = _tr_for(
            self,
            "status_manual_candidates_ready",
            count=len(event.candidates),
        )

    def _apply_manual_candidate_selection_continued(
        self,
        event: PresetManualCandidateSelectionContinuedEvent,
        updates: RuntimeUiUpdateBatch,
    ) -> None:
        if self._has_session_view(event.parent_session_tab_id):
            self._set_manual_candidates_processing(
                event.parent_session_tab_id,
                selected_count=len(event.selected_candidate_ids),
            )
            session_widgets = self._get_session_widgets(event.parent_session_tab_id)
            if session_widgets.preset_candidates_status_var is not None:
                session_widgets.preset_candidates_status_var.set(
                    _tr_for(
                        self,
                        "status_manual_candidates_continued",
                        count=len(event.selected_candidate_ids),
                    )
                )
        updates.workspace_task_lists.add(event.workspace_tab_id)
        updates.refresh_queue_summaries = True
        updates.status_message = _tr_for(
            self,
            "status_manual_candidates_continued",
            count=len(event.selected_candidate_ids),
        )

    def _apply_manual_candidate_selection_cleared(
        self,
        event: PresetManualCandidateSelectionClearedEvent,
        updates: RuntimeUiUpdateBatch,
    ) -> None:
        if self._has_session_view(event.parent_session_tab_id):
            self._clear_preset_manual_candidates(
                event.parent_session_tab_id,
                status_message=(
                    localize_runtime_message(event.message, _window_language(self))
                    if event.message
                    else _tr_for(self, "manual_candidates_empty")
                ),
            )
        if event.message:
            updates.status_message = localize_runtime_message(
                event.message,
                _window_language(self),
            )

    def _render_preset_manual_candidates(
        self,
        session_tab_id: str,
        candidates: Sequence[PresetCandidate],
        *,
        editable: bool,
        status_message: str,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        list_frame = session_widgets.preset_candidates_list_frame
        status_var = session_widgets.preset_candidates_status_var
        if list_frame is None or status_var is None:
            return

        for child in list_frame.winfo_children():
            child.destroy()
        session_widgets.preset_candidate_check_vars.clear()
        session_widgets.preset_candidate_ids = tuple(candidate.id for candidate in candidates)
        session_widgets.preset_candidates_editable = editable
        status_var.set(status_message)

        for row_index, candidate in enumerate(candidates):
            candidate_id = candidate.id
            checked_var = tk.BooleanVar(value=False)
            session_widgets.preset_candidate_check_vars[candidate_id] = checked_var
            checkbutton = ttk.Checkbutton(
                list_frame,
                variable=checked_var,
                command=lambda target_id=session_tab_id: (
                    self._refresh_manual_candidates_continue_button(target_id)
                ),
            )
            checkbutton.grid(
                row=row_index,
                column=0,
                sticky="nw",
                pady=self._ui_scale.padding(0, 8),
            )
            checkbutton.configure(state="normal" if editable else "disabled")
            label = ttk.Label(
                list_frame,
                text=self._format_preset_candidate(candidate),
                justify="left",
                wraplength=self._ui_scale.px(760),
            )
            label.grid(
                row=row_index,
                column=1,
                sticky="ew",
                padx=self._ui_scale.padding(8, 0),
                pady=self._ui_scale.padding(0, 8),
            )
        list_frame.columnconfigure(1, weight=1)
        self._refresh_manual_candidates_continue_button(session_tab_id)

    def _format_preset_candidate(self, candidate: PresetCandidate) -> str:
        evidence = candidate.evidence
        if isinstance(evidence, tuple):
            evidence_text = "\n".join(evidence)
        else:
            evidence_text = evidence
        return "\n".join(
            (
                _tr_for(
                    self,
                    "manual_candidate_header",
                    candidate_id=candidate.id,
                    title=candidate.title,
                    priority=candidate.priority,
                ),
                _tr_for(self, "manual_candidate_problem", value=candidate.problem),
                _tr_for(self, "manual_candidate_risk", value=candidate.risk),
                _tr_for(self, "manual_candidate_impact", value=candidate.impact),
                _tr_for(self, "manual_candidate_evidence", value=evidence_text),
            )
        )

    def _continue_preset_manual_candidates(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        selected_candidate_ids = tuple(
            candidate_id
            for candidate_id in session_widgets.preset_candidate_ids
            if session_widgets.preset_candidate_check_vars.get(candidate_id) is not None
            and session_widgets.preset_candidate_check_vars[candidate_id].get()
        )
        if not selected_candidate_ids:
            if session_widgets.preset_candidates_status_var is not None:
                session_widgets.preset_candidates_status_var.set(
                    _tr_for(self, "manual_candidates_select_required")
                )
            self._refresh_manual_candidates_continue_button(session_tab_id)
            return

        self._set_manual_candidates_processing(
            session_tab_id,
            selected_count=len(selected_candidate_ids),
        )
        continue_in_background = getattr(
            self._runtime,
            "continue_preset_manual_selection_in_background",
            None,
        )
        if not callable(continue_in_background):
            return
        continue_in_background(session_tab_id, selected_candidate_ids)
        self._set_status(
            _tr_for(
                self,
                "status_manual_candidates_continuing",
                count=len(selected_candidate_ids),
            )
        )

    def _set_manual_candidates_processing(
        self,
        session_tab_id: str,
        *,
        selected_count: int,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        session_widgets.preset_candidates_editable = False
        if session_widgets.preset_candidates_status_var is not None:
            session_widgets.preset_candidates_status_var.set(
                _tr_for(
                    self,
                    "manual_candidates_processing",
                    count=selected_count,
                )
            )
        self._set_manual_candidate_checkbuttons_enabled(session_widgets, enabled=False)
        if session_widgets.preset_candidates_continue_button is not None:
            session_widgets.preset_candidates_continue_button.configure(state="disabled")

    def _clear_preset_manual_candidates(
        self,
        session_tab_id: str,
        *,
        status_message: str | None = None,
    ) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        list_frame = session_widgets.preset_candidates_list_frame
        if list_frame is not None:
            for child in list_frame.winfo_children():
                child.destroy()
        session_widgets.preset_candidate_check_vars.clear()
        session_widgets.preset_candidate_ids = ()
        session_widgets.preset_candidates_editable = False
        if session_widgets.preset_candidates_status_var is not None:
            session_widgets.preset_candidates_status_var.set(
                status_message or _tr_for(self, "manual_candidates_empty")
            )
        if session_widgets.preset_candidates_continue_button is not None:
            session_widgets.preset_candidates_continue_button.configure(state="disabled")

    def _refresh_manual_candidates_continue_button(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        button = session_widgets.preset_candidates_continue_button
        if button is None:
            return
        has_selection = any(
            check_var.get()
            for check_var in session_widgets.preset_candidate_check_vars.values()
        )
        enabled = session_widgets.preset_candidates_editable and has_selection
        button.configure(state="normal" if enabled else "disabled")

    def _set_manual_candidate_checkbuttons_enabled(
        self,
        session_widgets: SessionWidgets,
        *,
        enabled: bool,
    ) -> None:
        list_frame = session_widgets.preset_candidates_list_frame
        if list_frame is None:
            return
        state = "normal" if enabled else "disabled"
        for child in list_frame.winfo_children():
            if isinstance(child, ttk.Checkbutton):
                child.configure(state=state)

    def _select_session_candidates_tab(self, session_tab_id: str) -> None:
        session_widgets = self._get_session_widgets(session_tab_id)
        if session_widgets.candidates_tab_frame is None:
            return
        session_widgets.body_notebook.select(session_widgets.candidates_tab_frame)
