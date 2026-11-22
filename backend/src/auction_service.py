from sqlalchemy import select,func
from fastapi import HTTPException
from .models import Consignment,VaseSample,AuctionSession,AuctionLot,Bid,Deal,Clock,PriceReview
from .common import lock,pack,add,audit,stamp
from .pricing import estimate,judge
FLOORS={'A':8000,'B':5500,'C':3000}
def session_create(s,d,u):
    clock=lock(s,Clock,1)
    if stamp(d.closes_at)<=clock.now:raise HTTPException(409,'场次必须晚于业务时钟结束')
    x=AuctionSession(code=d.code,opens_at=stamp(d.opens_at),closes_at=stamp(d.closes_at));audit(s,u,'建立拍卖场次 '+d.code);return add(s,x)
def offer(s,d,u):
    cons=lock(s,Consignment,d.consignment_id);session=lock(s,AuctionSession,d.session_id);clock=lock(s,Clock,1);sample=lock(s,VaseSample,d.sample_id)
    if sample.consignment_id!=cons.id or sample.state!='approved':raise HTTPException(409,'挂场须使用本到场批次已主管核定的留样等级')
    if session.closes_at<=clock.now:raise HTTPException(409,'场次已截止')
    if d.reserve_cents<FLOORS[sample.approved_grade]:raise HTTPException(409,'底价低于该等级最低筐价 '+str(FLOORS[sample.approved_grade]/100)+'元')
    occupied=s.scalar(select(func.coalesce(func.sum(AuctionLot.crates),0)).where(AuctionLot.consignment_id==cons.id,AuctionLot.state!='cancelled'))
    if occupied+d.crates>cons.crates:raise HTTPException(409,'到场筐数不足，已挂/已成交筐不可重复占用')
    x=AuctionLot(**d.model_dump(),grade_snapshot=sample.approved_grade);audit(s,u,'按等级挂场锁筐 '+d.code);return add(s,x)
def locked_lot(s,id):
    pre=s.get(AuctionLot,id)
    if not pre:raise HTTPException(404,'拍卖标的不存在')
    lock(s,Consignment,pre.consignment_id);session=lock(s,AuctionSession,pre.session_id);clock=lock(s,Clock,1);lot=lock(s,AuctionLot,id);return lot,session,clock
def top_bid(s,lot_id):return s.scalar(select(Bid).where(Bid.lot_id==lot_id,Bid.state=='active').order_by(Bid.unit_cents.desc(),Bid.id).limit(1))
def make_deal(s,lot,bid,clock):
    if s.scalar(select(Deal.id).where(Deal.lot_id==lot.id)):raise HTTPException(409,'该标的已有成交单，重试不会重复成交')
    lot.state='sold'
    return Deal(lot_id=lot.id,bid_id=bid.id,buyer=bid.buyer,crates=lot.crates,unit_cents=bid.unit_cents,original_grade=lot.grade_snapshot,effective_grade=lot.grade_snapshot,at=clock.now)
def bid(s,id,d,u):
    lot,session,clock=locked_lot(s,id)
    if lot.state!='offered' or not session.opens_at<=clock.now<session.closes_at:raise HTTPException(409,'标的未开场、已截止或已成交')
    highest=s.scalar(select(func.max(Bid.unit_cents)).where(Bid.lot_id==lot.id,Bid.state=='active'))or 0
    if d.unit_cents<lot.reserve_cents or d.unit_cents<=highest:raise HTTPException(409,'报价须达到底价并严格高于当前最高价')
    audit(s,u,'买方报价 '+d.buyer);return add(s,Bid(lot_id=lot.id,buyer=d.buyer,unit_cents=d.unit_cents,at=clock.now))
def close(s,id,u):
    lot,session,clock=locked_lot(s,id)
    if lot.state=='held':raise HTTPException(409,'标的已留筐待裁决，请先处理复核单')
    if lot.state!='offered':raise HTTPException(409,'只能截标一次')
    if clock.now<session.closes_at:raise HTTPException(409,'场次尚未到截止时间')
    best=top_bid(s,lot.id)
    if not best:raise HTTPException(409,'无有效出价，请撤下标的释放筐数')
    est=estimate(s,lot,session,best.unit_cents)
    if est['reason']:
        lot.state='held'
        review=PriceReview(lot_id=lot.id,bid_id=best.id,reason=est['reason'],estimate=est,created_at=clock.now)
        audit(s,u,'截标留筐复核 '+lot.code+' '+best.buyer+' '+str(best.unit_cents));return add(s,review)
    deal=make_deal(s,lot,best,clock);audit(s,u,'截标成交 '+lot.code);return add(s,deal)
def decide_review(s,id,d,u):
    pre=s.get(PriceReview,id)
    if not pre:raise HTTPException(404,'复核单不存在')
    lot,session,clock=locked_lot(s,pre.lot_id)
    review=lock(s,PriceReview,id)
    if review.state!='pending':raise HTTPException(409,'该复核已裁决，重试或并发裁决不会重复生效')
    best=top_bid(s,lot.id)
    if not best or best.id!=review.bid_id:raise HTTPException(409,'复核对应的报价已变化，旧裁决不能落到下一口价')
    review.decided_by=u.email;review.decided_at=clock.now;review.note=d.note
    if d.action=='approve':
        review.state='approved';deal=make_deal(s,lot,best,clock)
        audit(s,u,'主管裁决放行成交 '+lot.code+' '+best.buyer);return add(s,deal)
    review.state='revoked';lock(s,Bid,best.id).state='revoked'
    audit(s,u,'主管撤销异常报价 '+lot.code+' '+best.buyer+' '+str(best.unit_cents))
    nxt=top_bid(s,lot.id)
    if not nxt:
        lot.state='offered';audit(s,u,'异常报价全部撤完，放筐 '+lot.code);return pack(review)
    est=dict(review.estimate);reason,deviation=judge(est,nxt.unit_cents)
    est['bid_cents']=nxt.unit_cents;est['deviation']=deviation;est['reason']=reason
    if reason:
        nxt_review=PriceReview(lot_id=lot.id,bid_id=nxt.id,reason=reason,estimate=est,created_at=clock.now)
        audit(s,u,'下一口价仍异常，继续留筐复核 '+nxt.buyer+' '+str(nxt.unit_cents));return add(s,nxt_review)
    deal=make_deal(s,lot,nxt,clock);audit(s,u,'下一口价正常，按 '+str(nxt.unit_cents)+' 成交 '+lot.code);return add(s,deal)
def cancel(s,id,u):
    lot,session,clock=locked_lot(s,id)
    if lot.state!='offered' or top_bid(s,lot.id):raise HTTPException(409,'只有未成交且无有效出价标的可撤回')
    lot.state='cancelled';audit(s,u,'撤下未出价标的 '+lot.code);return pack(lot)
