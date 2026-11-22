from sqlalchemy import select,func,text,inspect
from .db import Base,engine,Session
from .models import *
from .auth import hashed

def migrate():
    cols={c['name']for c in inspect(engine).get_columns('bids')}
    with engine.begin()as c:
        if 'state' not in cols:c.execute(text("ALTER TABLE bids ADD COLUMN state VARCHAR(20)"))
        c.execute(text("UPDATE bids SET state='active' WHERE state IS NULL"))

def historical(s,code,variety,grade,crates,price,day,hour):
    c=s.scalar(select(Consignment).where(Consignment.code==code))
    if not c:c=Consignment(code=code,grower='露禾花圃',variety=variety,cut_at=day+'T04:30:00',arrived_at=day+'T07:30:00',crates=crates,stems_per_crate=40,note='历史成交批次，筐价留档');s.add(c);s.flush()
    sample=s.scalar(select(VaseSample).where(VaseSample.code=='S-'+code))
    if not sample:sample=VaseSample(consignment_id=c.id,code='S-'+code,maturity=.4,stem_cm=70,cold_hours=2,observed_days=8,state='approved',approved_grade=grade,review_note='历史已核级签');s.add(sample);s.flush()
    ses=s.scalar(select(AuctionSession).where(AuctionSession.code=='SES-'+code))
    if not ses:ses=AuctionSession(code='SES-'+code,opens_at=day+f'T{hour:02d}:00:00',closes_at=day+f'T{hour:02d}:50:00');s.add(ses);s.flush()
    lot=s.scalar(select(AuctionLot).where(AuctionLot.code=='LOT-'+code))
    if not lot:lot=AuctionLot(code='LOT-'+code,consignment_id=c.id,sample_id=sample.id,session_id=ses.id,crates=crates,reserve_cents=5500,state='sold',grade_snapshot=grade);s.add(lot);s.flush()
    b=s.scalar(select(Bid).where(Bid.lot_id==lot.id))
    if not b:b=Bid(lot_id=lot.id,buyer='历史花店',unit_cents=price,at=day+f'T{hour:02d}:45:00');s.add(b);s.flush()
    if not s.scalar(select(Deal).where(Deal.lot_id==lot.id)):s.add(Deal(lot_id=lot.id,bid_id=b.id,buyer=b.buyer,crates=crates,unit_cents=price,original_grade=grade,effective_grade=grade,revision=1,at=day+f'T{hour:02d}:50:00'));s.flush()

def seed():
    Base.metadata.create_all(engine)
    migrate()
    with Session()as s:
        for role in ('operator','supervisor','viewer'):
            if not s.scalar(select(User.id).where(User.email==role+'@chenlu.demo')):s.add(User(email=role+'@chenlu.demo',password_hash=hashed('Flowers2026!'),role=role))
        if not s.get(Clock,1):s.add(Clock(id=1,now='2026-11-18T08:00:00'))
        c=s.scalar(select(Consignment).where(Consignment.code=='FL031'))
        if not c:c=Consignment(code='FL031',grower='露禾花圃',variety='洋桔梗·晨雾白',cut_at='2026-11-18T04:30:00',arrived_at='2026-11-18T07:30:00',crates=18,stems_per_crate=40,note='18筐逐筐验收，筐贴FL031-01至18');s.add(c);s.flush()
        sample=s.scalar(select(VaseSample).where(VaseSample.code=='FLS31'))
        if not sample:sample=VaseSample(consignment_id=c.id,code='FLS31',maturity=.35,stem_cm=72,cold_hours=2,observed_days=6,state='approved',approved_grade='A',review_note='历史已核级签，样本后续观察6天');s.add(sample);s.flush()
        session=s.scalar(select(AuctionSession).where(AuctionSession.code=='HISTORY-031'))
        if not session:session=AuctionSession(code='HISTORY-031',opens_at='2026-11-18T07:40:00',closes_at='2026-11-18T07:50:00');s.add(session);s.flush()
        lot=s.scalar(select(AuctionLot).where(AuctionLot.code=='HIST-LOT031'))
        if not lot:lot=AuctionLot(code='HIST-LOT031',consignment_id=c.id,sample_id=sample.id,session_id=session.id,crates=2,reserve_cents=8000,state='sold',grade_snapshot='A');s.add(lot);s.flush()
        bid=s.scalar(select(Bid).where(Bid.lot_id==lot.id))
        if not bid:bid=Bid(lot_id=lot.id,buyer='巷口花店',unit_cents=10000,at='2026-11-18T07:45:00');s.add(bid);s.flush()
        deal=s.scalar(select(Deal).where(Deal.lot_id==lot.id))
        if not deal:deal=Deal(lot_id=lot.id,bid_id=bid.id,buyer=bid.buyer,crates=2,unit_cents=10000,original_grade='A',effective_grade='A',revision=1,at='2026-11-18T07:50:00');s.add(deal);s.flush()
        if not s.scalar(select(Dispute.id).where(Dispute.deal_id==deal.id)):s.add(Dispute(deal_id=deal.id,reason='历史留样仅6天，买方请求按留样复判',state='opened',sample_snapshot={c.name:getattr(sample,c.name)for c in sample.__table__.columns}))
        historical(s,'FL026','洋桔梗·晨雾白','B',4,6200,'2026-11-17',8)
        historical(s,'FL027','洋桔梗·晨雾白','A',3,9600,'2026-11-17',9)
        historical(s,'FL028','洋桔梗·晨雾白','A',5,10100,'2026-11-17',10)
        s.commit();return {m.__tablename__:s.scalar(select(func.count()).select_from(m))for m in (User,Consignment,VaseSample,AuctionSession,AuctionLot,Bid,Deal,Dispute,Settlement,PriceReview)}
