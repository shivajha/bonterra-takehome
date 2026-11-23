"""WF-5 / AC-16: competing approvals cannot overspend a fund.

Each thread uses its OWN database connection (like separate server processes),
and all threads start at the same moment via a barrier.
"""
import threading

import pytest

from cb import service as svc
from cb.auth import load_actor
from cb.db import connect
from cb.errors import ServiceError


def approve_concurrently(db_path, request_ids):
    barrier = threading.Barrier(len(request_ids))
    results = {}

    def worker(rid):
        c = connect(db_path)
        try:
            pm = load_actor(c, "cb-manager")
            barrier.wait()
            svc.approve_request(c, pm, rid)
            results[rid] = "APPROVED"
        except ServiceError as e:
            results[rid] = e.code
        finally:
            c.close()

    threads = [threading.Thread(target=worker, args=(rid,)) for rid in request_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def make_requests(conn, actors, amounts, fund_id="fund-cb-utility"):
    return [svc.create_request(conn, actors["cb-caseworker"], "part-cb-001", fund_id, "UTILITIES",
                               "Electric bill", a)["request"]["id"] for a in amounts]


@pytest.mark.parametrize("run", range(20))  # repeat to shake out timing-dependent bugs
def test_two_competing_approvals_one_wins(conn, db_path, actors, run):  # AC-16
    ids = make_requests(conn, actors, ["300.00", "300.00"])  # fund is $500
    results = approve_concurrently(db_path, ids)
    assert sorted(results.values()) == ["APPROVED", "INSUFFICIENT_FUNDS"]
    f = svc.get_funding_summary(conn, actors["cb-manager"], "fund-cb-utility")["funds"][0]
    assert f["committed"] == "300.00" and f["available"] == "200.00"


def test_many_competing_approvals_never_overspend(conn, db_path, actors):
    ids = make_requests(conn, actors, ["100.00"] * 12)  # only 5 fit in $500
    results = approve_concurrently(db_path, ids)
    assert list(results.values()).count("APPROVED") == 5
    assert list(results.values()).count("INSUFFICIENT_FUNDS") == 7
    f = svc.get_funding_summary(conn, actors["cb-manager"], "fund-cb-utility")["funds"][0]
    assert f["committed_cents"] == 50_000
    approved_sum = conn.execute(
        "SELECT SUM(amount_cents) FROM request WHERE fund_id = 'fund-cb-utility' AND status = 'APPROVED'"
    ).fetchone()[0]
    assert approved_sum == f["committed_cents"]


def test_same_request_approved_concurrently_only_once(conn, db_path, actors):  # WF-4 under concurrency
    rid = make_requests(conn, actors, ["100.00"])[0]
    barrier = threading.Barrier(5)
    outcomes = []

    def worker():
        c = connect(db_path)
        try:
            pm = load_actor(c, "cb-manager")
            barrier.wait()
            svc.approve_request(c, pm, rid)
            outcomes.append("APPROVED")
        except ServiceError as e:
            outcomes.append(e.code)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("APPROVED") == 1
    assert outcomes.count("INVALID_TRANSITION") == 4
    f = svc.get_funding_summary(conn, actors["cb-manager"], "fund-cb-utility")["funds"][0]
    assert f["committed"] == "100.00"