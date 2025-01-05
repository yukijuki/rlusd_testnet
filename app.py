from flask import Flask, render_template, request, redirect, url_for
from xrpl.clients import JsonRpcClient
from xrpl.models.requests import AccountInfo
from xrpl.wallet import generate_faucet_wallet, Wallet
from xrpl.models.transactions import Payment, TrustSet
from xrpl.models.amounts import IssuedCurrencyAmount
from xrpl.transaction import sign, autofill, submit_and_wait
from datetime import datetime, timedelta
import pytz
import json

app = Flask(__name__)

################################################################################
# GLOBAL CONFIGURATION & VARIABLES
################################################################################
testnet_client = JsonRpcClient("https://s.altnet.rippletest.net:51234/")

# The RLUSD issuer address on Testnet (Replace with the actual issuer address)
RLUSD_ISSUER = "rhK25WHDPaDzmDK5Zn9j4ocjWErtBtyiKK"  

# Global variable to store the wallet details
wallet_details = {}

# Global transaction history
global_transactions = []

################################################################################
# HELPER FUNCTIONS
################################################################################
def convert_utc_to_japan(utc_time):
    """Convert UTC timestamp from XRPL transaction to Japan time (UTC+9)."""
    utc_time_obj = datetime.strptime(utc_time, "%Y-%m-%d %H:%M:%S %Z")
    japan_timezone = pytz.timezone("Asia/Tokyo")
    japan_time = utc_time_obj.replace(tzinfo=pytz.utc).astimezone(japan_timezone)
    return japan_time.strftime("%Y-%m-%d %H:%M:%S")

def create_rlusd_wallet():
    """
    1. Create/fund a testnet wallet (XRP).
    2. Establish a TrustLine to RLUSD issuer.
    3. Return wallet address/seed/balance.
    """
    # 1) Generate wallet (funded by Testnet faucet for demonstration)
    wallet = generate_faucet_wallet(testnet_client)
    address = wallet.classic_address
    seed = wallet.seed

    # 2) Establish a trust line to RLUSD issuer
    trust_set_tx = TrustSet(
        account=address,
        limit_amount={
            "currency": "RLU",
            "issuer": RLUSD_ISSUER,
            "value": "1000000000",  # trust limit; set an appropriately large number
        }
    )

    trust_set_tx = autofill(trust_set_tx, testnet_client)
    signed_trust_set_tx = sign(trust_set_tx, wallet)
    submit_and_wait(signed_trust_set_tx, testnet_client)

    payment_tx = Payment(
        account=RLUSD_ISSUER,
        destination=address,
        amount=IssuedCurrencyAmount(
            currency="RLU",
            issuer=RLUSD_ISSUER,
            value="100"  # 100 RLU
            )
        )
    signed_payment = sign(autofill(payment_tx, testnet_client), RLUSD_ISSUER)
    response = submit_and_wait(signed_payment, testnet_client)
    print("Sent 100 RLU to sender:", response)

    # 3) Get account balance (in XRP) after trustline set
    account_info = AccountInfo(
        account=address,
        ledger_index="validated",
        strict=True
    )
    response = testnet_client.request(account_info)
    xrp_balance = float(response.result["account_data"]["Balance"]) / 1_000_000

    return {
        "address": address,
        "seed": seed,
        "balance": round(xrp_balance, 3),  # Remaining XRP in the wallet
    }

def send_rlusd(source_seed, destination_address, amount):
    """
    Send RLUSD from one wallet to another.
    `amount` is the RLUSD amount you want to send (as float or str).
    """
    source_wallet = Wallet.from_seed(source_seed)

    # Construct a Payment transaction for an issued currency (RLUSD)
    payment_tx = Payment(
        account=source_wallet.classic_address,
        destination=destination_address,
        amount={
            "currency": "RLU",
            "issuer": RLUSD_ISSUER,
            "value": str(amount)
        }
    )
    print("------ payment_tx: ", payment_tx)

    # Autofill, sign, submit
    payment_tx = autofill(payment_tx, testnet_client)
    signed_tx = sign(payment_tx, source_wallet)
    response = submit_and_wait(signed_tx, testnet_client)
    return response

################################################################################
# FLASK ROUTES
################################################################################
@app.route("/")
def home():
    return render_template(
        "home.html",
        wallet_details=wallet_details
    )

@app.route("/merchant")
def merchant():
    return render_template(
        "merchant.html",
        wallet_details=wallet_details,
        transactions=global_transactions
    )

@app.route("/create_wallet", methods=["GET"])
def create_wallet_route():
    """
    Create a wallet that:
      1. Is funded with Testnet XRP
      2. Establishes a TrustLine for RLUSD
    """
    global wallet_details
    wallet_details = create_rlusd_wallet()
    return redirect(url_for("home"))

@app.route("/send_rlusd", methods=["POST"])
def send_rlusd_route():
    """
    Send RLUSD to a destination.
    """
    global wallet_details, global_transactions

    try:
        source_seed = wallet_details["seed"]
        destination_address = request.form["destination_address"]
        amount = float(request.form["amount"])  # RLUSD amount

        # Send RLUSD
        response = send_rlusd(source_seed, destination_address, amount)
        tx_hash = response.result["hash"]
        print("Payment Succeeded:", tx_hash)

        # Fetch updated balance (XRP) - RLUSD will show up on the issuer side.
        account_info = AccountInfo(
            account=wallet_details["address"],
            ledger_index="validated",
            strict=True
        )
        account_response = testnet_client.request(account_info)
        wallet_details["balance"] = round(
            float(account_response.result["account_data"]["Balance"]) / 1_000_000,
            3
        )

        # Transaction time
        ripple_epoch = datetime(2000, 1, 1, 0, 0, 0)
        txn_time_seconds = response.result["tx_json"].get("date", 0)
        txn_time = ripple_epoch + timedelta(seconds=txn_time_seconds)

        # Record the transaction
        transaction = {
            "id": tx_hash,
            "recipient": destination_address,
            "rlusd_amount": amount,
            "fee": float(response.result["tx_json"]["Fee"]) / 1_000_000,
            "timestamp": convert_utc_to_japan(txn_time.strftime('%Y-%m-%d %H:%M:%S UTC')),
            "details": response.result,
        }
        global_transactions.append(transaction)

        message = (
            f"Transaction Complete! \n\n"
            f"Transaction Amount: {amount} RLUSD \n"
            f"Tx Hash: {tx_hash}"
        )
        print("----- Banner Message: ", message)

        return render_template(
            "merchant.html",
            wallet_details=wallet_details,
            message=message,
            transactions=global_transactions,
            transaction=transaction
        )

    except Exception as e:
        print("Error:", str(e))
        return render_template(
            "merchant.html",
            wallet_details=wallet_details,
            error=str(e),
            transactions=global_transactions
        )

if __name__ == "__main__":
    app.run(debug=True)
