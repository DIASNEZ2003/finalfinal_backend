import os
import json
import firebase_admin
from firebase_admin import credentials, auth, db
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List, Dict, Any
import time
import httpx
import math
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------
# 1. SETUP & INITIALIZATION
# ---------------------------------------------------------

# Try to read the credentials from Render's Environment Variables first
firebase_keys_str = os.environ.get('FIREBASE_SERVICE_ACCOUNT')

if firebase_keys_str:
    # If it finds the environment variable (meaning it's on Render), use it
    cred_dict = json.loads(firebase_keys_str)
    cred = credentials.Certificate(cred_dict)
else:
    # If it doesn't find it (meaning it's on your local laptop), use the file
    cred = credentials.Certificate("serviceAccountKey.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://final-future-d1547-default-rtdb.firebaseio.com/' 
    })

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------
# 2. UTILITY: PHILIPPINE TIME
# ---------------------------------------------------------
def get_ph_time():
    """Returns current timestamp in milliseconds for Philippines (UTC+8)"""
    now_utc = datetime.now(timezone.utc)
    ph_time = now_utc + timedelta(hours=8)
    return int(ph_time.timestamp() * 1000)

# ---------------------------------------------------------
# 3. DATA MODELS
# ---------------------------------------------------------

class BatchSchema(BaseModel):
    batchName: str
    dateCreated: str
    expectedCompleteDate: str 
    startingPopulation: int
    vitaminBudget: Optional[float] = 0.0
    penCount: Optional[int] = 5
    averageChickWeight: Optional[float] = 50.0
    status: Optional[str] = None

class BatchUpdateSchema(BaseModel):
    batchName: Optional[str] = None
    dateCreated: Optional[str] = None
    expectedCompleteDate: Optional[str] = None
    status: Optional[str] = None
    startingPopulation: Optional[int] = None
    penCount: Optional[int] = None
    averageChickWeight: Optional[float] = None

class UserRegisterSchema(BaseModel):
    firstName: str
    lastName: str
    username: str
    password: str
    role: Optional[str] = "user"

class MessageSchema(BaseModel):
    recipientUid: str
    text: str

class EditMessageSchema(BaseModel):
    targetUid: str
    messageId: str
    newText: str

class DeleteMessageSchema(BaseModel):
    targetUid: str
    messageId: str

class SalesRecordSchema(BaseModel):
    batchId: str  
    buyerName: str
    address: str
    quantity: int
    pricePerChicken: float
    dateOfPurchase: str

class EditSalesRecordSchema(BaseModel):
    batchId: str
    saleId: str
    buyerName: str
    address: str
    quantity: int
    pricePerChicken: float
    dateOfPurchase: str

class ExpenseSchema(BaseModel):
    batchId: str  
    category: str
    feedType: Optional[str] = None
    itemName: str
    description: Optional[str] = ""
    amount: float
    quantity: float
    purchaseCount: Optional[float] = 1.0  
    remaining: Optional[float] = None
    unit: str
    date: str

class EditExpenseSchema(BaseModel):
    batchId: str
    expenseId: str
    category: str
    feedType: Optional[str] = None
    itemName: str
    description: Optional[str] = ""
    amount: float
    quantity: float
    purchaseCount: Optional[float] = 1.0  
    remaining: Optional[float] = None
    unit: str
    date: str

class UpdateFeedCategorySchema(BaseModel):
    batchId: str
    expenseId: str
    category: str
    feedType: str 

class VitaminLogSchema(BaseModel):
    batchId: str
    day: int
    vitaminName: str
    actualAmount: float

class WeightLogSchema(BaseModel):
    batchId: str
    date: str
    day: int
    averageWeight: float
    unit: str = "g"
    updatedBy: Optional[str] = "Unknown"
    updaterName: Optional[str] = "Unknown"

class DeleteWeightLogSchema(BaseModel):
    batchId: str
    date: str

class PersonnelSchema(BaseModel):
    firstName: str
    lastName: str
    age: str
    address: str
    status: str
    photoUrl: Optional[str] = ""

class EditPersonnelSchema(BaseModel):
    personnelId: str
    firstName: str
    lastName: str
    age: str
    address: str
    status: str
    photoUrl: Optional[str] = ""

class VitaminForecastRequestSchema(BaseModel):
    batchId: str
    months: Optional[int] = 3 

# ---------------------------------------------------------
# 4. KNOWLEDGE BASE (FEED & VITAMIN LOGIC)
# ---------------------------------------------------------

FEED_LOGIC_TEMPLATE = [
    (range(1, 2), 35.0, "Booster"),
    (range(2, 4), 35.0, "Booster"),
    (range(4, 7), 45.0, "Booster"),
    (range(7, 11), 55.0, "Booster"),
    (range(11, 15), 85.0, "Starter"),
    (range(15, 21), 115.0, "Starter"),
    (range(21, 26), 145.0, "Finisher"),
    (range(26, 31), 170.0, "Finisher"),
]

MEDICATION_DB = {
    "vetracin": {"adult_dose": 100.0, "unit": "g"}, 
    "amox": {"adult_dose": 100.0, "unit": "g"},
    "doxy": {"adult_dose": 100.0, "unit": "g"},
    "electrolytes": {"adult_dose": 100.0, "unit": "g"},
    "vitamin": {"adult_dose": 100.0, "unit": "g"},
    "multivitamins": {"adult_dose": 100.0, "unit": "ml"},
    "broncho": {"adult_dose": 120.0, "unit": "ml"},
    "gumboro": {"fixed_dose": 1.0, "unit": "vial"},
    "newcastle": {"fixed_dose": 1.0, "unit": "vial"},
    "ncd": {"fixed_dose": 1.0, "unit": "vial"},
}

# ---------------------------------------------------------
# 5. CALCULATION ENGINES
# ---------------------------------------------------------

def get_estimated_fcr(day: int) -> float:
    if day <= 5: return 1.3
    if day <= 12: return 1.4
    if day <= 21: return 1.5
    return 1.7

def generate_forecast_data(population: int):
    forecast_data = []
    print(f"--- Recalculating Feed for Pop: {population} ---")
    for day in range(1, 31):
        f_match = next((item for item in FEED_LOGIC_TEMPLATE if day in item[0]), None)
        if f_match:
            grams_per_bird = f_match[1]
            target_kilos = (grams_per_bird * population) / 1000.0
            
            forecast_data.append({
                "day": day,
                "feedType": f_match[2],
                "targetKilos": round(target_kilos, 2),
                "gramsPerBird": grams_per_bird 
            })
    return forecast_data

def generate_pen_populations(starting_pop: int, pen_count: int):
    """Divides the starting population evenly among the specified number of pens"""
    if pen_count <= 0: pen_count = 1
    per_pen = starting_pop // pen_count
    remainder = starting_pop % pen_count
    pens = {}
    for i in range(1, pen_count + 1):
        pens[f"pen_{i}"] = per_pen + 1 if i <= remainder else per_pen
    return pens

def generate_pen_forecasts(feed_forecast: list, pen_populations: dict):
    """Calculates exactly how much feed each pen needs per day based on its specific population"""
    pen_forecasts = {}
    for pen_id, pop in pen_populations.items():
        pen_forecasts[pen_id] = {}
        for f in feed_forecast:
            day = f["day"]
            grams = f.get("gramsPerBird", 0)
            feed_type = f.get("feedType", "Booster")
            kilos = round((pop * grams) / 1000.0, 2)
            
            pen_forecasts[pen_id][f"day_{day}"] = {
                "targetKilos": kilos,
                "feedType": feed_type
            }
    return pen_forecasts

def generate_weight_forecast(start_weight: float, population: int, feed_forecast: list):
    weight_data = []
    current_weight_g = start_weight
    target_days = [1] + list(range(3, 31, 3))
    
    for day in range(1, 31):
        day_feed_data = next((f for f in feed_forecast if f["day"] == day), None)
        if day_feed_data:
            daily_feed_g = day_feed_data["gramsPerBird"]
            fcr = get_estimated_fcr(day)
            daily_gain_g = daily_feed_g / fcr
            current_weight_g += daily_gain_g
            if day in target_days:
                total_flock_weight_kg = (current_weight_g * population) / 1000.0
                weight_data.append({
                    "day": f"Day {day}",
                    "weight": round(total_flock_weight_kg, 2),
                    "avgWeight": int(current_weight_g),
                    "fcr": fcr,
                    "unit": "kg"
                })
    return weight_data

def calculate_vitamin_trends(batches: list):
    """Calculate vitamin usage trends from historical batches"""
    vitamin_data = {}
    
    sorted_batches = sorted(batches, key=lambda x: x.get('dateCreated', ''))
    
    for batch in sorted_batches:
        batch_date = batch.get('dateCreated', '')
        batch_pop = batch.get('startingPopulation', 1000)
        batch_vitamins = {}
        
        if batch.get('expenses'):
            for exp_id, exp in batch.get('expenses').items():
                if exp.get('category') in ['Vitamins', 'Medications']:
                    vitamin_name = exp.get('itemName', 'Others')
                    quantity = float(exp.get('quantity', 0)) * float(exp.get('purchaseCount', 1))
                    
                    if vitamin_name not in batch_vitamins:
                        batch_vitamins[vitamin_name] = 0
                    batch_vitamins[vitamin_name] += quantity
        
        for vit_name, amount in batch_vitamins.items():
            if vit_name not in vitamin_data:
                vitamin_data[vit_name] = []
            
            rate_per_bird = amount / batch_pop
            vitamin_data[vit_name].append({
                "date": batch_date,
                "population": batch_pop,
                "amount": amount,
                "rate_per_bird": rate_per_bird
            })
    
    trends = {}
    for vit_name, data in vitamin_data.items():
        if len(data) >= 2:
            first_rate = data[0]["rate_per_bird"]
            last_rate = data[-1]["rate_per_bird"]
            avg_rate = sum(d["rate_per_bird"] for d in data) / len(data)
            
            if first_rate > 0:
                trend_pct = ((last_rate - first_rate) / first_rate) * 100
            else:
                trend_pct = 0
            
            trends[vit_name] = {
                "historical": data,
                "avg_rate_per_bird": avg_rate,
                "trend_percentage": round(trend_pct, 1),
                "trend_direction": "up" if trend_pct > 5 else "down" if trend_pct < -5 else "stable",
                "confidence": "high" if len(data) >= 3 else "medium" if len(data) >= 2 else "low"
            }
    
    return trends

def deactivate_other_active_batches(current_batch_id=None):
    """If we make a batch active, turn off all others."""
    try:
        ref = db.reference('global_batches')
        snapshot = ref.get()
        if snapshot:
            for bid, bdata in snapshot.items():
                if isinstance(bdata, dict) and bdata.get('status') == 'active' and bid != current_batch_id:
                    ref.child(bid).update({"status": "inactive"})
                    print(f"Deactivated batch: {bid}")
    except Exception as e:
        print(f"Error deactivating batches: {e}")

def activate_next_inactive_batch():
    """Finds the oldest 'inactive' batch and turns it 'active'."""
    try:
        ref = db.reference('global_batches')
        snapshot = ref.get()
        if snapshot:
            inactive_batches = [
                (bid, bdata) for bid, bdata in snapshot.items()
                if isinstance(bdata, dict) and bdata.get('status') == 'inactive'
            ]
            inactive_batches.sort(key=lambda x: x[1].get('dateCreated', '9999-99-99'))
            
            if inactive_batches:
                next_id = inactive_batches[0][0]
                next_name = inactive_batches[0][1].get('batchName')
                ref.child(next_id).update({"status": "active"})
                print(f"Auto-Activated next batch: {next_name}")
                return True
    except Exception as e:
        print(f"Error auto-activating next batch: {e}")
    return False

# ---------------------------------------------------------
# 6. API ENDPOINTS
# ---------------------------------------------------------

@app.post("/register-user")
async def register_user(data: dict, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        user_ref = db.reference(f'users/{uid}')
        user_ref.set({
            "firstName": data.get("firstName"),
            "lastName": data.get("lastName"),
            "fullName": f"{data.get('firstName')} {data.get('lastName')}",
            "username": data.get("username"),
            "role": "admin",
            "status": "online",
            "dateCreated": get_ph_time()
        })
        return {"status": "success", "uid": uid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/verify-login")
async def verify_login(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        decoded_token = auth.verify_id_token(token)
        uid = decoded_token['uid']
        user_data = db.reference(f'users/{uid}').get()
        if not user_data or user_data.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Access denied")
        return {"status": "success", "user": user_data}
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid session")

@app.post("/admin-create-user")
async def admin_create_user(data: UserRegisterSchema, authorization: str = Header(None)):
    try:
        email = f"{data.username}@poultry.com"
        user_record = auth.create_user(email=email, password=data.password, display_name=data.username)
        user_ref = db.reference(f'users/{user_record.uid}')
        user_ref.set({
            "firstName": data.firstName,
            "lastName": data.lastName,
            "fullName": f"{data.firstName} {data.lastName}",
            "username": data.username,
            "role": data.role, 
            "status": "offline",
            "dateCreated": get_ph_time()
        })
        return {"status": "success", "uid": user_record.uid}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/get-users")
async def get_users(authorization: str = Header(None)):
    try:
        ref_users = db.reference('users')
        snapshot = ref_users.get()
        users_list = []
        if snapshot:
            for uid, data in snapshot.items():
                data['uid'] = uid
                users_list.append(data)
        return users_list
    except Exception as e:
        return []

@app.delete("/admin-delete-user/{target_uid}")
async def admin_delete_user(target_uid: str, authorization: str = Header(None)):
    try:
        auth.delete_user(target_uid)
        db.reference(f'users/{target_uid}').delete()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/create-batch")
async def create_batch(data: BatchSchema, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    
    try:
        auth.verify_id_token(token)
        
        if data.status == 'active':
            deactivate_other_active_batches()
            final_status = 'active'
        elif data.status in ['inactive', 'completed']:
            final_status = data.status
        else:
            ref_all = db.reference('global_batches')
            snapshot = ref_all.get()
            has_active_batch = False
            if snapshot:
                for _, val in snapshot.items():
                    if isinstance(val, dict) and val.get('status') == 'active':
                        has_active_batch = True
                        break
            final_status = "inactive" if has_active_batch else "active"

        ref_batch = db.reference('global_batches')
        new_batch_ref = ref_batch.push()
        
        pen_count = data.penCount if data.penCount else 5
        pen_pops = generate_pen_populations(data.startingPopulation, pen_count)
        feed_forecast = generate_forecast_data(data.startingPopulation)
        pen_forecasts = generate_pen_forecasts(feed_forecast, pen_pops)
        
        weight_forecast = generate_weight_forecast(
            data.averageChickWeight if data.averageChickWeight else 50.0,
            data.startingPopulation,
            feed_forecast
        )
        
        batch_data = {
            "batchName": data.batchName,
            "dateCreated": data.dateCreated,
            "expectedCompleteDate": data.expectedCompleteDate,
            "startingPopulation": data.startingPopulation,
            "vitaminBudget": data.vitaminBudget,
            "penCount": pen_count,
            "averageChickWeight": data.averageChickWeight,
            "status": final_status,
            "feedForecast": feed_forecast,
            "weightForecast": weight_forecast,
            "pen_populations": pen_pops,
            "pen_forecasts": pen_forecasts
        }
        
        new_batch_ref.set(batch_data)
        return {"status": "success", "message": f"Batch created as {final_status}"}
    
    except Exception as e:
        print(f"CREATE BATCH ERROR: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/get-batches")
async def get_batches(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        auth.verify_id_token(token)
        snapshot = db.reference('global_batches').get()
        batches_list = []
        if snapshot:
            for key, val in snapshot.items():
                if isinstance(val, dict):
                    val['id'] = key
                    batches_list.append(val)
        return batches_list
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/update-batch/{batch_id}")
async def update_batch(batch_id: str, data: BatchUpdateSchema, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    
    try:
        auth.verify_id_token(token)
        
        if data.status == "active":
            deactivate_other_active_batches(current_batch_id=batch_id)

        ref_batch = db.reference(f'global_batches/{batch_id}')
        current_batch_data = ref_batch.get() or {}
        
        updates = {}
        if data.batchName is not None: updates["batchName"] = data.batchName
        if data.dateCreated is not None: updates["dateCreated"] = data.dateCreated
        if data.expectedCompleteDate is not None: updates["expectedCompleteDate"] = data.expectedCompleteDate
        if data.status is not None: updates["status"] = data.status

        needs_forecast_recalc = False
        new_pop = data.startingPopulation if data.startingPopulation is not None else current_batch_data.get('startingPopulation', 0)
        new_pens = data.penCount if data.penCount is not None else current_batch_data.get('penCount', 5)
        new_weight = data.averageChickWeight if data.averageChickWeight is not None else current_batch_data.get('averageChickWeight', 50.0)

        if data.startingPopulation is not None:
            updates["startingPopulation"] = data.startingPopulation
            needs_forecast_recalc = True
        if data.penCount is not None:
            updates["penCount"] = data.penCount
            needs_forecast_recalc = True
        if data.averageChickWeight is not None:
            updates["averageChickWeight"] = data.averageChickWeight
            needs_forecast_recalc = True

        if needs_forecast_recalc:
            pen_pops = generate_pen_populations(new_pop, new_pens)
            new_feed_forecast = generate_forecast_data(new_pop)
            pen_forecasts = generate_pen_forecasts(new_feed_forecast, pen_pops)
            new_weight_forecast = generate_weight_forecast(new_weight, new_pop, new_feed_forecast)
            
            updates["feedForecast"] = new_feed_forecast
            updates["pen_populations"] = pen_pops
            updates["pen_forecasts"] = pen_forecasts
            updates["weightForecast"] = new_weight_forecast
            updates["vitaminForecast"] = None

        if updates:
            ref_batch.update(updates)

        if data.status == "completed":
            activate_next_inactive_batch()

        return {"status": "success"}
    
    except Exception as e:
        print(f"UPDATE BATCH ERROR: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/update-batch-settings/{batch_id}")
async def update_batch_settings(batch_id: str, data: BatchUpdateSchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        
        batch_ref = db.reference(f'global_batches/{batch_id}')
        current_batch_data = batch_ref.get() or {}
        
        updates = {}
        
        needs_forecast_recalc = False
        new_pop = data.startingPopulation if data.startingPopulation is not None else current_batch_data.get('startingPopulation', 0)
        new_pens = data.penCount if data.penCount is not None else current_batch_data.get('penCount', 5)
        new_weight = data.averageChickWeight if data.averageChickWeight is not None else current_batch_data.get('averageChickWeight', 50.0)

        if data.startingPopulation is not None:
            updates["startingPopulation"] = data.startingPopulation
            needs_forecast_recalc = True
        if data.penCount is not None: 
            updates["penCount"] = data.penCount
            needs_forecast_recalc = True
        if data.averageChickWeight is not None: 
            updates["averageChickWeight"] = data.averageChickWeight
            needs_forecast_recalc = True
            
        if needs_forecast_recalc:
            pen_pops = generate_pen_populations(new_pop, new_pens)
            new_feed_forecast = generate_forecast_data(new_pop)
            pen_forecasts = generate_pen_forecasts(new_feed_forecast, pen_pops)
            new_weight_forecast = generate_weight_forecast(new_weight, new_pop, new_feed_forecast)
            
            updates["feedForecast"] = new_feed_forecast
            updates["pen_populations"] = pen_pops
            updates["pen_forecasts"] = pen_forecasts
            updates["weightForecast"] = new_weight_forecast
            updates["vitaminForecast"] = None
        
        if data.status is not None:
            if data.status == "active":
                deactivate_other_active_batches(current_batch_id=batch_id)
            updates["status"] = data.status
            
        if updates:
            batch_ref.update(updates)
            
        return {"status": "success"}
    except Exception as e:
        print(f"UPDATE SETTINGS ERROR: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/delete-batch/{batch_id}")
async def delete_batch(batch_id: str, authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = authorization.split("Bearer ")[1]
    try:
        auth.verify_id_token(token)
        db.reference(f'global_batches/{batch_id}').delete()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# 7. MESSAGING
# ---------------------------------------------------------
@app.post("/admin-send-message")
async def admin_send_message(data: MessageSchema, authorization: str = Header(None)):
    try:
        recipient_data = db.reference(f'users/{data.recipientUid}').get()
        current_status = "sent"
        if recipient_data and isinstance(recipient_data, dict) and recipient_data.get("status") == "online":
            current_status = "delivered"
        db.reference(f'chats/{data.recipientUid}').push({
            "text": data.text,
            "sender": "admin",
            "timestamp": get_ph_time(),
            "isEdited": False,
            "status": current_status,
            "seen": False
        })
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin-edit-message")
async def admin_edit_message(data: EditMessageSchema, authorization: str = Header(None)):
    try:
        ref_msg = db.reference(f'chats/{data.targetUid}/{data.messageId}')
        ref_msg.update({"text": data.newText, "isEdited": True})
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin-delete-message")
async def admin_delete_message(data: DeleteMessageSchema, authorization: str = Header(None)):
    try:
        db.reference(f'chats/{data.targetUid}/{data.messageId}').delete()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# 8. EXPENSES & SALES
# ---------------------------------------------------------
@app.post("/add-expense")
async def add_expense(data: ExpenseSchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        db.reference(f'global_batches/{data.batchId}/expenses').push({
            **data.dict(exclude={"batchId"}),
            "timestamp": get_ph_time()
        })
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/edit-expense")
async def edit_expense(data: EditExpenseSchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        ref_exp = db.reference(f'global_batches/{data.batchId}/expenses/{data.expenseId}')
        ref_exp.update({
            "category": data.category,
            "feedType": data.feedType,
            "itemName": data.itemName,
            "description": data.description,
            "amount": data.amount,
            "quantity": data.quantity,
            "purchaseCount": data.purchaseCount, 
            "remaining": data.remaining,
            "unit": data.unit,
            "date": data.date
        })
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/delete-expense/{batch_id}/{expense_id}")
async def delete_expense(batch_id: str, expense_id: str, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        db.reference(f'global_batches/{batch_id}/expenses/{expense_id}').delete()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/get-expenses/{batch_id}")
async def get_expenses(batch_id: str, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        snapshot = db.reference(f'global_batches/{batch_id}/expenses').get()
        return [{"id": k, **v} for k, v in snapshot.items()] if snapshot else []
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.patch("/update-expense-category")
async def update_expense_category(data: UpdateFeedCategorySchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        ref_exp = db.reference(f'global_batches/{data.batchId}/expenses/{data.expenseId}')
        ref_exp.update({"category": data.category, "feedType": data.feedType})
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/add-sale")
async def add_sale(data: SalesRecordSchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        db.reference(f'global_batches/{data.batchId}/sales').push({
            **data.dict(exclude={"batchId"}),
            "totalAmount": data.quantity * data.pricePerChicken,
            "timestamp": get_ph_time()
        })
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/edit-sale")
async def edit_sale(data: EditSalesRecordSchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        ref_sale = db.reference(f'global_batches/{data.batchId}/sales/{data.saleId}')
        ref_sale.update({
            "buyerName": data.buyerName,
            "address": data.address,
            "quantity": data.quantity,
            "pricePerChicken": data.pricePerChicken,
            "totalAmount": data.quantity * data.pricePerChicken,
            "dateOfPurchase": data.dateOfPurchase
        })
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/delete-sale/{batch_id}/{sale_id}")
async def delete_sale(batch_id: str, sale_id: str, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        db.reference(f'global_batches/{batch_id}/sales/{sale_id}').delete()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/get-sales/{batch_id}")
async def get_sales(batch_id: str, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        snapshot = db.reference(f'global_batches/{batch_id}/sales').get()
        return [{"id": k, **v} for k, v in snapshot.items()] if snapshot else []
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# 9. FORECASTING & INVENTORY
# ---------------------------------------------------------

@app.get("/get-vitamin-forecast/{batch_id}")
async def get_vitamin_forecast(batch_id: str, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        
        batch_ref = db.reference(f'global_batches/{batch_id}')
        batch_data = batch_ref.get()
        
        if not batch_data:
            raise HTTPException(status_code=404, detail="Batch not found")
        
        all_batches_ref = db.reference('global_batches')
        all_batches = all_batches_ref.get()
        
        completed_batches = []
        if all_batches:
            for bid, bdata in all_batches.items():
                if isinstance(bdata, dict) and bdata.get('status') == 'completed':
                    completed_batches.append(bdata)
        
        trends = calculate_vitamin_trends(completed_batches)
        
        if not trends:
            return {
                "batchName": batch_data.get('batchName'),
                "population": batch_data.get('startingPopulation', 1000),
                "forecast": [],
                "trends": {},
                "message": "No historical vitamin data available",
                "generated": get_ph_time()
            }
        
        return {
            "batchName": batch_data.get('batchName'),
            "population": batch_data.get('startingPopulation', 1000),
            "forecast": [],  
            "trends": trends,
            "generated": get_ph_time()
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/get-vitamin-monthly-forecast")
async def get_vitamin_monthly_forecast(months: int = 3, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        
        all_batches_ref = db.reference('global_batches')
        all_batches = all_batches_ref.get()
        
        if not all_batches:
            return {"forecast": [], "message": "No batch data available"}
        
        monthly_usage = {}
        
        for bid, bdata in all_batches.items():
            if not isinstance(bdata, dict):
                continue

            if bdata.get('status') in ['completed', 'active'] and bdata.get('expenses'):
                date_created = bdata.get('dateCreated', '')
                if date_created:
                    try:
                        month_key = date_created[:7]
                        if month_key not in monthly_usage:
                            monthly_usage[month_key] = {
                                "month": month_key,
                                "vitamins": {},
                                "total_batches": 0
                            }
                        
                        monthly_usage[month_key]["total_batches"] += 1
                        
                        for exp_id, exp in bdata.get('expenses').items():
                            if exp.get('category') in ['Vitamins', 'Medications']:
                                vit_name = exp.get('itemName', 'Others')
                                quantity = float(exp.get('quantity', 0)) * float(exp.get('purchaseCount', 1))
                                
                                if vit_name not in monthly_usage[month_key]["vitamins"]:
                                    monthly_usage[month_key]["vitamins"][vit_name] = 0
                                
                                monthly_usage[month_key]["vitamins"][vit_name] += quantity
                    except Exception:
                        continue
        
        if not monthly_usage:
            return {
                "historical": [],
                "forecast": [],
                "vitamin_breakdown": {},
                "trend": {
                    "direction": "stable",
                    "rate": 0,
                    "average_monthly": 0
                },
                "message": "No vitamin expense data available"
            }
        
        sorted_months = sorted(monthly_usage.keys())
        time_series = []
        vitamin_totals = {}
        
        for month in sorted_months:
            month_data = monthly_usage[month]
            total_month = sum(month_data["vitamins"].values())
            
            time_series.append({
                "month": month,
                "total": round(total_month, 2),
                "batches": month_data["total_batches"]
            })
            
            for vit_name, amount in month_data["vitamins"].items():
                if vit_name not in vitamin_totals:
                    vitamin_totals[vit_name] = []
                vitamin_totals[vit_name].append({
                    "month": month,
                    "amount": round(amount, 2)
                })
        
        forecast = []
        growth_rate = 0
        
        if len(time_series) >= 2:
            first_total = time_series[0]["total"]
            last_total = time_series[-1]["total"]
            
            if first_total > 0:
                growth_rate = (last_total - first_total) / len(time_series)
            
            if last_total > 0:
                last_month = time_series[-1]["month"]
                last_year = int(last_month[:4])
                last_month_num = int(last_month[5:7])
                
                for i in range(1, months + 1):
                    next_month_num = last_month_num + i
                    next_year = last_year
                    if next_month_num > 12:
                        next_month_num -= 12
                        next_year += 1
                    
                    next_month = f"{next_year}-{str(next_month_num).zfill(2)}"
                    forecast_total = last_total + (growth_rate * i)
                    
                    forecast.append({
                        "month": next_month,
                        "forecast": round(max(forecast_total, 0), 2),
                        "confidence": "high" if len(time_series) >= 3 else "medium"
                    })
        
        return {
            "historical": time_series,
            "forecast": forecast,
            "vitamin_breakdown": vitamin_totals,
            "trend": {
                "direction": "up" if growth_rate > 0 else "down" if growth_rate < 0 else "stable",
                "rate": round(abs(growth_rate), 2),
                "average_monthly": round(sum(t["total"] for t in time_series) / len(time_series), 2) if time_series else 0
            }
        }
        
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/get-inventory-forecast/{batch_id}")
async def get_inventory_forecast(batch_id: str, authorization: str = Header(None)):
    return await get_vitamin_forecast(batch_id, authorization)

@app.get("/get-feed-forecast/{batch_id}")
async def get_feed_forecast(batch_id: str, authorization: str = Header(None)):
    try:
        batch_ref = db.reference(f'global_batches/{batch_id}')
        batch_data = batch_ref.get()
        if not batch_data: 
            raise HTTPException(status_code=404, detail="Batch not found")
        
        population = batch_data.get('startingPopulation', 1000)
        start_weight = batch_data.get('averageChickWeight', 50.0) 
        
        new_feed_forecast = generate_forecast_data(population)
        new_weight_forecast = generate_weight_forecast(start_weight, population, new_feed_forecast)
        
        batch_ref.update({
            'feedForecast': new_feed_forecast,
            'weightForecast': new_weight_forecast
        })
        
        return {
            "batchName": batch_data.get('batchName'), 
            "feedForecast": new_feed_forecast,
            "weightForecast": new_weight_forecast
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# 10. WEATHER — Accurate for Bacolod City, Philippines
#
#  Open-Meteo FREE API — no key needed.
#  Fetches every 60s from Dashboard.jsx, saves to Firebase
#  current_weather node so all clients get live updates.
#
#  Fields fetched:
#    temperature_2m         → actual air temp (°C)
#    apparent_temperature   → feels-like / heat index (°C) ← computed by meteo
#    relative_humidity_2m   → humidity (%)
#    weather_code           → WMO code (0=clear … 99=thunderstorm)
#    is_day                 → 1=daytime, 0=night
#    precipitation          → mm in the last hour
#    wind_speed_10m         → km/h
#    uv_index               → 0–11+ scale
# ---------------------------------------------------------

@app.get("/get-temperature")
async def get_temperature(lat: float = 10.6765, lon: float = 122.9509):
    """
    Fetches current weather from Open-Meteo for the given coordinates.
    Defaults to Bacolod City, Negros Occidental, Philippines.
    Saves the result to Firebase current_weather node and returns it.
    """

    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m"
        f",apparent_temperature"
        f",relative_humidity_2m"
        f",weather_code"
        f",is_day"
        f",precipitation"
        f",wind_speed_10m"
        f",uv_index"
        f"&timezone=Asia%2FManila"          # PHT — ensures correct is_day
        f"&wind_speed_unit=kmh"
        f"&forecast_days=1"
    )

    weather_payload = {
        "temperature": 0,
        "feelsLike": 0,
        "humidity": 0,
        "precipitation": 0.0,
        "windSpeed": 0.0,
        "uvIndex": 0,
        "weatherCode": 0,
        "isDay": 1,
        "unit": "°C",
        "last_updated": get_ph_time()
    }

    # 1. Fetch live from Open-Meteo
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url)
            if response.status_code == 200:
                current = response.json().get("current", {})

                weather_payload = {
                    "temperature":   round(current.get("temperature_2m", 0), 1),
                    "feelsLike":     round(current.get("apparent_temperature", 0), 1),
                    "humidity":      current.get("relative_humidity_2m", 0),
                    "precipitation": round(current.get("precipitation", 0.0), 1),
                    "windSpeed":     round(current.get("wind_speed_10m", 0.0), 1),
                    "uvIndex":       current.get("uv_index", 0),
                    "weatherCode":   current.get("weather_code", 0),
                    "isDay":         current.get("is_day", 1),
                    "unit":          "°C",
                    "last_updated":  get_ph_time()
                }

                # Save to Firebase (best-effort — don't crash if Firebase token expired)
                try:
                    db.reference('current_weather').set(weather_payload)
                    print(f"[Weather] {weather_payload['temperature']}°C feels {weather_payload['feelsLike']}°C | {weather_payload['humidity']}% RH | Code {weather_payload['weatherCode']}")
                except Exception as fb_err:
                    print(f"[Weather] Firebase save failed: {fb_err}")

                return weather_payload

    except Exception as api_err:
        print(f"[Weather] Open-Meteo fetch failed: {api_err}")

    # 2. Fallback — read last known value from Firebase
    try:
        db_data = db.reference('current_weather').get()
        if db_data:
            print("[Weather] Serving cached Firebase data")
            return db_data
    except Exception as fb_read_err:
        print(f"[Weather] Firebase read failed: {fb_read_err}")

    # 3. Final fallback — return zeroed payload so server stays alive
    return weather_payload

# ---------------------------------------------------------
# 11. ADMIN MASTER RECORDS
# ---------------------------------------------------------

@app.get("/get-all-records")
async def get_all_records(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    try:
        batches = db.reference('global_batches').get()
        all_records = []

        if not batches:
            return []

        for b_id, b_data in batches.items():
            if not isinstance(b_data, dict):
                continue
            
            b_name = b_data.get('batchName', 'Unnamed Batch')

            m_logs = b_data.get('mortality_logs', {})
            if m_logs:
                for date, log in m_logs.items():
                    total = int(log.get('am', 0)) + int(log.get('pm', 0))
                    all_records.append({
                        "id": f"mort-{b_id}-{date}",
                        "type": "Mortality",
                        "date": date,
                        "timestamp": log.get('timestamp', 0),
                        "title": f"Batch: {b_name}",
                        "subtitle": f"Mortality: {total} heads (AM: {log.get('am')}, PM: {log.get('pm')})",
                        "user": log.get('updaterName', 'System')
                    })

            f_logs = b_data.get('feed_logs', {})
            if f_logs:
                forecast = b_data.get('feedForecast', [])
                for date, log in f_logs.items():
                    start_date = datetime.strptime(b_data.get('dateCreated'), "%Y-%m-%d")
                    log_date = datetime.strptime(date, "%Y-%m-%d")
                    day_num = (log_date - start_date).days + 1
                    
                    feed_type = "Feed"
                    for f in forecast:
                        if f.get('day') == day_num:
                            feed_type = f.get('feedType', 'Feed')
                            break

                    all_records.append({
                        "id": f"feed-{b_id}-{date}",
                        "type": "Feed",
                        "date": date,
                        "timestamp": log.get('timestamp', 0),
                        "title": f"Batch: {b_name}",
                        "subtitle": f"{feed_type}: {float(log.get('am', 0)) + float(log.get('pm', 0))} kg",
                        "user": log.get('updaterName', 'System')
                    })

            v_logs = b_data.get('daily_vitamin_logs', {})
            if v_logs:
                for date, log in v_logs.items():
                    all_records.append({
                        "id": f"vit-{b_id}-{date}",
                        "type": "Vitamins",
                        "date": date,
                        "timestamp": log.get('timestamp', 0),
                        "title": f"Batch: {b_name}",
                        "subtitle": f"Supplement: {float(log.get('am_amount', 0)) + float(log.get('pm_amount', 0))} units",
                        "user": log.get('updaterName', 'System')
                    })

            w_logs = b_data.get('weight_logs', {})
            if w_logs:
                for date, log in w_logs.items():
                    all_records.append({
                        "id": f"weight-{b_id}-{date}",
                        "type": "Weight",
                        "date": date,
                        "timestamp": log.get('timestamp', 0),
                        "title": f"Batch: {b_name}",
                        "subtitle": f"Average Weight: {log.get('averageWeight')} {log.get('unit', 'g')}",
                        "user": log.get('updaterName', 'System')
                    })

        all_records.sort(key=lambda x: x['timestamp'], reverse=True)
        return all_records

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ---------------------------------------------------------
# 12. PERSONNEL MANAGEMENT
# ---------------------------------------------------------

@app.post("/add-personnel")
async def add_personnel(data: PersonnelSchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        ref_personnel = db.reference('personnel')
        new_ref = ref_personnel.push()
        new_ref.set({
            "firstName": data.firstName,
            "lastName": data.lastName,
            "fullName": f"{data.firstName} {data.lastName}",
            "age": data.age,
            "address": data.address,
            "status": data.status,
            "photoUrl": data.photoUrl,
            "dateAdded": get_ph_time()
        })
        return {"status": "success", "id": new_ref.key}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/get-personnel")
async def get_personnel(authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        snapshot = db.reference('personnel').get()
        if snapshot:
            return [{"id": k, **v} for k, v in snapshot.items()]
        return []
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/edit-personnel")
async def edit_personnel(data: EditPersonnelSchema, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        ref_p = db.reference(f'personnel/{data.personnelId}')
        update_data = {
            "firstName": data.firstName,
            "lastName": data.lastName,
            "fullName": f"{data.firstName} {data.lastName}",
            "age": data.age,
            "address": data.address,
            "status": data.status
        }
        if data.photoUrl:
            update_data["photoUrl"] = data.photoUrl
            
        ref_p.update(update_data)
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/delete-personnel/{personnel_id}")
async def delete_personnel(personnel_id: str, authorization: str = Header(None)):
    try:
        token = authorization.split("Bearer ")[1]
        auth.verify_id_token(token)
        db.reference(f'personnel/{personnel_id}').delete()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
