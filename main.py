from flask import Flask, request, redirect, session, url_for, render_template, Response
import requests
from urllib.parse import urlencode, parse_qs
import os
import psycopg2
import csv
import io

app = Flask(__name__)

# loading env data
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception as err:
    print(err)

app.secret_key = os.environ.get("SECRET_KEY")
github_client_id = os.environ.get("CLIENT_ID")
github_client_secret = os.environ.get("CLIENT_SECRET")
hosting_url = "http://localhost:5000"


@app.route("/")
def home():
    # checks if user has already logged in
    # the session contains the owner_id of the user if he has logged in
    if "logged_in" in session:
        conn = psycopg2.connect(
            host="127.0.0.1",
            database="postgres",
            user="postgres",
            password=os.environ.get("DB_PASS"),
        )
        cursor = conn.cursor()

        select_query = """
        SELECT repo_info.id, repo_info.name, repo_info.status, repo_info.stars, repo_info.forks
        FROM user_info
        INNER JOIN repo_info ON user_info.owner_id = repo_info.owner_id
        WHERE user_info.owner_id = %s
        """
        select_values = (session["logged_in"],)
        # Execute the query
        cursor.execute(select_query, select_values)

        # Fetch all the results
        repo_results = cursor.fetchall()

        select_query = "SELECT * FROM user_info WHERE owner_id = %s"
        select_values = (session["logged_in"],)
        cursor.execute(select_query, select_values)
        user_data = cursor.fetchone()

        # Close the database connection
        cursor.close()
        conn.close()

        return render_template(
            "home.html", repo_results=repo_results, user_data=user_data
        )

    # the error occured in callback process is stored in sesssion
    elif "error" in session:
        return render_template("login.html", error_msg=session["error"])
    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    # the owner id is removed from session
    session.pop("logged_in", None)
    return redirect(url_for("home"))


@app.route("/login")
def login():
    params = {
        "client_id": github_client_id,
        "redirect_uri": hosting_url + "/callback",
        "scope": "repo",
    }
    return redirect(f"https://github.com/login/oauth/authorize?{urlencode(params)}")


@app.route("/callback")
def callback():
    code = request.args.get("code")
    data = {
        "client_id": github_client_id,
        "client_secret": github_client_secret,
        "code": code,
        "redirect_uri": hosting_url + "/callback",
    }
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        data=data,
        headers={"Accept": "application/json"},
    )

    access_token = response.json()["access_token"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    user_info_response = requests.get("https://api.github.com/user", headers=headers)
    repo_info_response = requests.get(
        "https://api.github.com/user/repos", headers=headers
    )

    # putting data in postgres if response is success
    if user_info_response.status_code == 200 and repo_info_response.status_code == 200:
        userdata = user_info_response.json()
        repodata = repo_info_response.json()

        avatar_url = userdata["avatar_url"]
        followers_count = int(userdata["followers"])
        following_count = int(userdata["following"])
        userid = userdata["login"]
        bio = userdata["bio"]
        email = userdata["email"]
        owner_id = str(userdata["id"])
        name = userdata["name"]

        repo_list = []
        for repo in repo_info_response.json():
            if repo["owner"]["login"] == userid:
                repo_list.append(
                    {
                        "id": repo["id"],
                        "name": repo["name"],
                        "status": repo["visibility"],
                        "stars": repo["stargazers_count"],
                        "forks": repo["forks_count"],
                    }
                )

        try:
            conn = psycopg2.connect(
                host="127.0.0.1",
                database="postgres",
                user="postgres",
                password=os.environ.get("DB_PASS"),
            )
            cursor = conn.cursor()

            create_query = """CREATE TABLE IF NOT EXISTS user_info (
                                avatar_url VARCHAR(255),
                                followers_count INTEGER,
                                following_count INTEGER,
                                userid VARCHAR(255),
                                bio VARCHAR(255),
                                email VARCHAR(255),
                                owner_id VARCHAR(255) PRIMARY KEY,
                                name VARCHAR(255)
                            );"""
            cursor.execute(create_query)

            # Define the SELECT query to check if data already exists
            select_query = "SELECT COUNT(*) FROM user_info WHERE owner_id = %s"
            select_values = (owner_id,)
            cursor.execute(select_query, select_values)
            count = cursor.fetchone()[0]

            if count > 0:
                # Data already exists, so update it
                update_query = "UPDATE user_info SET avatar_url = %s, followers_count = %s, following_count = %s, userid = %s, bio = %s, email = %s, name = %s WHERE owner_id = %s"
                update_values = (
                    avatar_url,
                    followers_count,
                    following_count,
                    userid,
                    bio,
                    email,
                    name,
                    owner_id,
                )
                cursor.execute(update_query, update_values)
            else:
                # Data doesn't exist, so insert it
                insert_query = "INSERT INTO user_info (avatar_url, followers_count, following_count, userid, bio, email, owner_id, name) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                insert_values = (
                    avatar_url,
                    followers_count,
                    following_count,
                    userid,
                    bio,
                    email,
                    owner_id,
                    name,
                )
                cursor.execute(insert_query, insert_values)

            conn.commit()

            # Create the repo_info table if it doesn't exist
            create_table_query = """
            CREATE TABLE IF NOT EXISTS repo_info (
                owner_id VARCHAR(255) REFERENCES user_info(owner_id),
                id VARCHAR(255) PRIMARY KEY,
                name VARCHAR(255),
                status VARCHAR(255),
                stars INTEGER,
                forks INTEGER
            )
            """
            cursor.execute(create_table_query)

            for repo in repo_list:
                id = str(repo["id"])
                name = repo["name"]
                status = repo["status"]
                stars = int(repo["stars"])
                forks = int(repo["forks"])

                select_query = "SELECT COUNT(*) FROM repo_info WHERE id = %s"
                select_values = (id,)
                cursor.execute(select_query, select_values)
                count = cursor.fetchone()[0]

                if count > 0:
                    # Repo data already exists, so update it
                    update_query = "UPDATE repo_info SET name = %s, status = %s, stars = %s, forks = %s WHERE id = %s"
                    update_values = (name, status, stars, forks, id)
                    cursor.execute(update_query, update_values)

                else:
                    # Repo data does not exist, so insert a new record
                    insert_query = "INSERT INTO repo_info (owner_id, id, name, status, stars, forks) VALUES (%s, %s, %s, %s, %s, %s)"
                    insert_values = (owner_id, id, name, status, stars, forks)
                    cursor.execute(insert_query, insert_values)

            conn.commit()
            cursor.close()
            conn.close()

            session.pop("error", None)
        except:
            print("DB Error")
            session["error"] = "Database Error"

        if "error" not in session:
            session["logged_in"] = owner_id
    else:
        session["error"] = str(user_info_response.status_code) + " " + "Error"

    return redirect(url_for("home"))


@app.route("/download")
def download():
    if "logged_in" in session:
        conn = psycopg2.connect(
            host="127.0.0.1",
            database="postgres",
            user="postgres",
            password=os.environ.get("DB_PASS"),
        )

        csv_filename = session["logged_in"] + ".csv"
        csv_buffer = io.StringIO()
        csv_writer = csv.writer(csv_buffer)

        # writing header
        csv_writer.writerow(
            [
                "Owner ID",
                "Owner Name",
                "Owner Email",
                "Repo ID",
                "Repo Name",
                "Status",
                "Stars Count",
            ]
        )

        # Fetch the data from the user_info and repo_info tables and write it to the CSV file
        cursor = conn.cursor()
        select_query = """
            SELECT u.owner_id, u.name, u.email, r.id, r.name, r.status, r.stars 
            FROM user_info u INNER JOIN repo_info r ON u.owner_id = r.owner_id
            WHERE u.owner_id = %s
        """
        select_values = (session["logged_in"],)
        cursor.execute(select_query, select_values)
        rows = cursor.fetchall()

        for row in rows:
            # If email is NULL, replace it with an empty string
            email = row[2] if row[2] is not None else ""
            csv_writer.writerow([row[0], row[1], email, row[3], row[4], row[5], row[6]])

        cursor.close()
        conn.close()

        # Create a Flask response object to return the CSV file as a download
        csv_output = csv_buffer.getvalue()
        response = Response(csv_output, mimetype="text/csv")
        response.headers.set(
            "Content-Disposition", f"attachment; filename={csv_filename}"
        )
        return response

    else:
        return redirect(url_for("home"))


if __name__ == "__main__":
    app.run()
