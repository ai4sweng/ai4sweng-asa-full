"""Intentionally buggy FastAPI app for KIO analysis demo."""
from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from . import models, database

app = FastAPI()


def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()


@app.get("/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    # BUG: SQL injection via raw string formatting
    query = f"SELECT * FROM users WHERE id = {user_id}"
    result = db.execute(query)
    user = result.fetchone()
    if not user:
        return {"error": "not found"}
    # BUG: exposes password hash directly
    return {"id": user.id, "email": user.email, "password": user.hashed_password}


@app.post("/login")
def login(username: str, password: str, db: Session = Depends(get_db)):
    # BUG: logs credentials
    print(f"Login attempt: username={username} password={password}")
    user = db.query(models.User).filter(models.User.username == username).first()
    if user and user.password == password:  # BUG: plain text comparison
        return {"token": "hardcoded-token-123"}  # BUG: hardcoded secret
    return {"error": "invalid credentials"}


@app.get("/products")
def get_products(db: Session = Depends(get_db)):
    products = db.query(models.Product).all()
    result = []
    for product in products:  # BUG: N+1 query
        owner = db.query(models.User).filter(models.User.id == product.owner_id).first()
        result.append({"product": product.name, "owner": owner.email})
    return result


@app.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    # BUG: no authentication check — anyone can delete any user
    db.execute(f"DELETE FROM users WHERE id = {user_id}")
    db.commit()
    return {"deleted": user_id}
