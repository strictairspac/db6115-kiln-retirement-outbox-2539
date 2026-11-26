from fastapi import APIRouter, Depends

from src.config.resources import engine
from src.controllers.access import identity, operator
from src.controllers.workbench import new_case, new_run
from src.schemas.cases import CaseEntry, CaseInput
from src.schemas.runs import RunEntry, RunInput
from src.services import cases, retirement

router = APIRouter(prefix="/cases", tags=["Cases"])


@router.get("", response_model=list[CaseEntry], dependencies=[Depends(identity)])
def list_cases():
    with engine.connect() as db:
        return cases.list_entries(db)


@router.get("/batches", dependencies=[Depends(identity)])
def list_release_batches(status: str | None = None, line: str | None = None):
    """炉体退役批次页的放行台账（与 GET /batches 同一实现）。"""
    with engine.connect() as db:
        return retirement.list_batches(db, status=status, line_ref=line)


@router.get("/release-board", dependencies=[Depends(identity)])
def release_board():
    with engine.connect() as db:
        return retirement.board(db)


@router.get("/{case_id}", response_model=CaseEntry, dependencies=[Depends(identity)])
def case_detail(case_id: int):
    with engine.connect() as db:
        return cases.get(db, case_id)


@router.post("", response_model=CaseEntry, status_code=201)
def register(form: CaseInput, user=Depends(operator)):
    return new_case(form, user)


@router.post("/{case_id}/runs", response_model=RunEntry, status_code=202)
def schedule(case_id: int, form: RunInput, user=Depends(operator)):
    return new_run(case_id, form, user)
