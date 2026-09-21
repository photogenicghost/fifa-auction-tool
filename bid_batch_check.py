"""Check bid ordering, per-bid validation, cutoff and whole-batch rollback."""
import os
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

with tempfile.TemporaryDirectory(prefix='auction-batch-') as directory:
    os.environ.update(DATA_DIR=directory, ADMIN_PASSWORD='batch-check-only',
                      SESSION_SECRET='batch-check-session-secret-000000000000', COOKIE_SECURE='false')
    import main
    connection = main.db()
    connection.execute("INSERT INTO users VALUES(1,'test','Synthetic User','synthetic@example.com',100)")
    connection.execute("INSERT INTO prizes VALUES(1,'test','Synthetic Prize',1,'SYNTHETIC','')")
    deadline = time.time()+300
    connection.execute("INSERT INTO auctions(id,mode,prize_id,status,ends_at) VALUES(1,'test',1,'OPEN',?)", (deadline,))
    generation = connection.execute("SELECT v FROM settings WHERE k='session_generation_test'").fetchone()[0]
    request = SimpleNamespace(session={'uid':1, 'participant_mode':'test', 'participant_generation':generation})
    stale = SimpleNamespace(session={'uid':1})
    entries = [(request,10,1), (request,9,1), (request,101,1), (stale,11,1), (request,11,99), (request,11,1)]
    result = main.commit_bids(entries)
    assert result[0] is None and result[5] is None
    assert all(isinstance(error,str) for error in result[1:5])
    assert [r[0] for r in connection.execute('SELECT amount FROM bids ORDER BY id')] == [10,11]
    connection.execute("CREATE TRIGGER fail_batch BEFORE INSERT ON bids WHEN NEW.amount=13 BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    result = main.commit_bids([(request,12,1), (request,13,1)])
    assert all(isinstance(error,str) for error in result), 'Failed transaction acknowledged a bid'
    assert connection.execute('SELECT MAX(amount) FROM bids').fetchone()[0] == 11
    connection.execute('DROP TRIGGER fail_batch')
    with patch.object(main.time, 'time', side_effect=[deadline-.001, deadline]):
        result = main.commit_bids([(request,12,1), (request,13,1)])
    assert result[0] is None and 'Time is up' in result[1]
    assert connection.execute('SELECT MAX(amount) FROM bids').fetchone()[0] == 12
    assert connection.execute('SELECT balance FROM users').fetchone()[0] == 100
    connection.close()
print('PASS: arrival order, duplicate/over-budget/stale rejection, mixed valid bids, transaction rollback and per-bid deadline')
