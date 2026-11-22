import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sqlalchemy import select
from .models import Deal,AuctionLot,Consignment
MIN_COMPARABLES=2
DEVIATION_THRESHOLD=0.30
GRADES=('A','B','C')
def training_rows(s,opens_at):
    q=(select(Deal,AuctionLot,Consignment).join(AuctionLot,Deal.lot_id==AuctionLot.id).join(Consignment,AuctionLot.consignment_id==Consignment.id).where(Deal.at<opens_at).order_by(Deal.id))
    return s.execute(q).all()
def estimate(s,lot,session,bid_cents=None):
    cons=s.get(Consignment,lot.consignment_id)
    rows=training_rows(s,session.opens_at)
    comps=[r for r in rows if r.Consignment.variety==cons.variety and r.AuctionLot.grade_snapshot==lot.grade_snapshot]
    estimate_cents=None;model=None
    if len(rows)>=4:
        varieties=sorted({r.Consignment.variety for r in rows}|{cons.variety})
        def features(variety,grade,crates):return [float(variety==v)for v in varieties]+[float(grade==g)for g in GRADES]+[float(crates)]
        x=np.array([features(r.Consignment.variety,r.AuctionLot.grade_snapshot,r.AuctionLot.crates)for r in rows]);y=np.array([r.Deal.unit_cents for r in rows],dtype=float)
        m=GradientBoostingRegressor(n_estimators=60,max_depth=2,random_state=35).fit(x,y)
        estimate_cents=int(round(float(m.predict([features(cons.variety,lot.grade_snapshot,lot.crates)])[0])));model='GradientBoostingRegressor'
    elif rows:
        pool=[r.Deal.unit_cents for r in comps]or[r.Deal.unit_cents for r in rows]
        estimate_cents=int(round(float(np.median(pool))));model='median'
    deviation=round((bid_cents-estimate_cents)/estimate_cents,4)if estimate_cents and bid_cents is not None else None
    reason=None
    if len(comps)<MIN_COMPARABLES:reason='few_comparables'
    elif deviation is not None and abs(deviation)>DEVIATION_THRESHOLD:reason='abnormal_price'
    return{'model':model,'trained_rows':len(rows),'variety':cons.variety,'grade':lot.grade_snapshot,'crates':lot.crates,'estimate_cents':estimate_cents,'comparable_count':len(comps),'min_comparables':MIN_COMPARABLES,'deviation_threshold':DEVIATION_THRESHOLD,'comparables':[{'deal_id':r.Deal.id,'lot_code':r.AuctionLot.code,'variety':r.Consignment.variety,'grade':r.AuctionLot.grade_snapshot,'crates':r.AuctionLot.crates,'unit_cents':r.Deal.unit_cents,'at':r.Deal.at}for r in comps],'bid_cents':bid_cents,'deviation':deviation,'reason':reason,'session_opens_at':session.opens_at}
def judge(est,bid_cents):
    deviation=round((bid_cents-est['estimate_cents'])/est['estimate_cents'],4)if est.get('estimate_cents')else None
    reason=None
    if est['comparable_count']<est['min_comparables']:reason='few_comparables'
    elif deviation is not None and abs(deviation)>est['deviation_threshold']:reason='abnormal_price'
    return reason,deviation
