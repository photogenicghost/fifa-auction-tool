"""Isolated timer regressions. Optional --serve opens a disposable UI fixture on port 8015."""
import os, tempfile, time, sys, re, subprocess, httpx
from pathlib import Path
from unittest.mock import patch
with tempfile.TemporaryDirectory(prefix='auction-timer-') as data:
    os.environ.update(DATA_DIR=data,ADMIN_PASSWORD='timer-check-only',SESSION_SECRET='timer-check-session-secret-000000000000',COOKIE_SECURE='false')
    import main
    from fastapi.testclient import TestClient
    from load_test import fixture,field
    admin=TestClient(main.app);rep=TestClient(main.app);guest=TestClient(main.app)
    admin.post('/admin/login',data={'password':os.environ['ADMIN_PASSWORD']})
    preview=admin.post('/admin/import/preview',data={'action':'replace'},files={'workbook':('test.xlsx',fixture(2,3))})
    assert 'Import complete' in admin.post('/admin/import/confirm',data={'token':field(preview.text,'token')}).text
    rep.post('/login',data={'email':'load001@example.com'})
    def state():return guest.get('/api/display').json()['auction']
    def bid(n):return rep.post('/bid',data={'amount':n,'auction_id':state()['id']},headers={'Accept':'application/json'})
    admin.post('/admin/load',data={'prize_id':1});a=state();assert a['status']=='READY' and a['ends_at'] is None
    assert bid(1).status_code==400
    assert 'before loading another' in admin.post('/admin/load',data={'prize_id':1}).text
    assert state()['id']==a['id']
    assert guest.post('/admin/start',data={'auction_id':a['id']},follow_redirects=False).status_code==303
    assert 'item changed' in admin.post('/admin/start',data={'auction_id':99999}).text
    with guest.websocket_connect('/ws') as ws:
        admin.post('/admin/start',data={'auction_id':a['id']});assert ws.receive_text()=='update'
    deadline=state()['ends_at'];assert 29 < deadline-time.time() <= 30
    assert 'already started' in admin.post('/admin/start',data={'auction_id':a['id']}).text
    assert state()['ends_at']==deadline
    main.init();assert state()['ends_at']==deadline
    assert 'still open' in admin.post('/admin/close/preview').text
    assert 'Wait until' in admin.post('/admin/close',data={'auction_id':a['id'],'bid_id':0}).text
    original_cookies=httpx.Cookies(rep.cookies)
    with patch.object(main.time,'time',return_value=deadline-.001):assert bid(10).status_code==200
    with patch.object(main.time,'time',return_value=deadline):
        assert bid(11).status_code==400
        assert state()['status']=='ENDED'
        assert rep.get('/api/auction').json()['auction']['status']=='ENDED'
    rep.cookies=original_cookies
    c=main.db();c.execute('UPDATE auctions SET ends_at=? WHERE id=?',(time.time()-1,a['id']));c.close()
    assert 'before loading another' in admin.post('/admin/load',data={'prize_id':1}).text
    assert bid(12).status_code==400
    assert rep.get('/api/auction').json()['balance']==1000000
    preview=admin.post('/admin/close/preview');award={'auction_id':field(preview.text,'auction_id'),'bid_id':field(preview.text,'bid_id')}
    admin.post('/admin/close',data=award);admin.post('/admin/close',data=award)
    assert rep.get('/api/auction').json()['balance']==999990
    assert len(rep.get('/api/auction').json()['wins'])==1
    # Cancellation keeps stock and balances; stale cancel cannot cancel another auction.
    admin.post('/admin/load',data={'prize_id':1});next_id=state()['id']
    admin.post('/admin/cancel',data={'auction_id':a['id']});assert state()['status']=='READY'
    admin.post('/admin/start',data={'auction_id':next_id});assert bid(20).status_code==200
    admin.post('/admin/cancel',data={'auction_id':next_id});assert state()['status']=='CANCELLED'
    assert rep.get('/api/auction').json()['balance']==999990
    # No-bid expiry does not consume stock.
    admin.post('/admin/load',data={'prize_id':1});empty=state()['id'];admin.post('/admin/start',data={'auction_id':empty})
    c=main.db();c.execute('UPDATE auctions SET ends_at=? WHERE id=?',(time.time()-1,empty));c.close()
    admin.post('/admin/close',data={'auction_id':empty,'bid_id':0})
    c=main.db();assert c.execute('SELECT quantity FROM prizes WHERE id=1').fetchone()[0]==2;c.close()
    # Early end freezes bidding, retains the winner, and requires separate award confirmation.
    admin.post('/admin/load',data={'prize_id':1});early=state()['id']
    assert 'Start the auction' in admin.post('/admin/end',data={'auction_id':early}).text
    assert state()['status']=='READY'
    admin.post('/admin/start',data={'auction_id':early});original_deadline=state()['ends_at']
    for unauthorized in (guest,rep):
        assert unauthorized.post('/admin/end',data={'auction_id':early},follow_redirects=False).status_code==303
    assert state()['ends_at']==original_deadline
    assert 'auction changed' in admin.post('/admin/end',data={'auction_id':a['id']}).text
    assert state()['ends_at']==original_deadline
    assert bid(25).status_code==200
    highest=state()['high_bid_id']
    with guest.websocket_connect('/ws') as ws:
        admin.post('/admin/end',data={'auction_id':early});assert ws.receive_text()=='update'
    ended=state();assert ended['status']=='ENDED' and ended['ends_at']<original_deadline
    assert ended['high']==25 and ended['high_bid_id']==highest
    assert bid(26).status_code==400
    admin.post('/admin/end',data={'auction_id':early})
    assert state()['ends_at']==ended['ends_at'], 'Repeated early end must not move the cutoff'
    main.init();assert state()['status']=='ENDED'
    assert rep.get('/api/auction').json()['balance']==999990
    c=main.db();assert c.execute('SELECT quantity FROM prizes WHERE id=1').fetchone()[0]==2;c.close()
    assert 'already started' in admin.post('/admin/start',data={'auction_id':early}).text
    preview=admin.post('/admin/close/preview');award={'auction_id':field(preview.text,'auction_id'),'bid_id':field(preview.text,'bid_id')}
    admin.post('/admin/close',data=award);admin.post('/admin/close',data=award)
    assert rep.get('/api/auction').json()['balance']==999965
    assert len(rep.get('/api/auction').json()['wins'])==2
    # Ending an empty auction early also preserves stock.
    admin.post('/admin/load',data={'prize_id':1});empty_early=state()['id']
    admin.post('/admin/start',data={'auction_id':empty_early})
    admin.post('/admin/end',data={'auction_id':empty_early})
    admin.post('/admin/close',data={'auction_id':empty_early,'bid_id':0})
    c=main.db();assert c.execute('SELECT quantity FROM prizes WHERE id=1').fetchone()[0]==1;c.close()
    assert 'End Bidding Early' in admin.get('/admin').text
    # Exercise the shared countdown logic with a controlled client clock.
    clock_js=re.search(r'<script>(.*?)</script>',main.auction_clock_script(),re.S)[1]
    harness="""const assert=require('node:assert/strict');let now=0;
    const ids=['countdown','end-bidding-early','review-award','submit','amount','next'];
    const elements=Object.fromEntries(ids.map(id=>[id,{disabled:false,textContent:''}]));
    global.document={getElementById:id=>elements[id]||null};
    global.performance={now:()=>now};global.setInterval=()=>{};
    """+clock_js+"""
    updateAuctionClock({auction:{id:1,status:'READY',ends_at:null},server_now:100});
    assert.equal(elements['end-bidding-early'].disabled,true);
    updateAuctionClock({auction:{id:1,status:'OPEN',ends_at:130},server_now:100});
    assert.equal(elements['end-bidding-early'].disabled,false);
    assert.equal(elements['review-award'].disabled,true);
    now=5000;
    updateAuctionClock({auction:{id:1,status:'ENDED',ends_at:105},server_now:105});
    assert.equal(elements['end-bidding-early'].disabled,true);
    assert.equal(elements['review-award'].disabled,false);
    assert.equal(elements['submit'].disabled,true);
    assert.equal(elements['countdown'].textContent,'0 seconds - bidding closed');
    """
    clock_path=Path(data)/'clock-check.js';clock_path.write_text(harness,encoding='utf-8')
    subprocess.run(['node',str(clock_path)],check=True)
    # Compile the actual inline scripts shipped by the app, not old extracted copies.
    for route,client in [('/admin',admin),('/auction',rep),('/display',guest)]:
        scripts=re.findall(r'<script>(.*?)</script>',client.get(route).text,re.S)
        path=Path(data)/(route.strip('/')+'.js');path.write_text('\n'.join(scripts),encoding='utf-8')
        subprocess.run(['node','--check',str(path)],check=True)
    print('PASS: load/start, access control, countdown persistence, exact deadline, award, cancellation, early-end permissions/cutoff/award, no-bid stock, inline JavaScript.',flush=True)
    if '--serve' in sys.argv:
        import uvicorn
        print('Disposable UI fixture: http://127.0.0.1:8015, admin password timer-check-only',flush=True)
        uvicorn.run(main.app,host='127.0.0.1',port=8015)
