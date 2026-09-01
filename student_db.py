import os
import mysql.connector
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Connect using environment variables
my_db = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME")
)

my_cursor = my_db.cursor()# my_cursor.execute("CREATE TABLE IF NOT EXISTS student (student_id INT  PRIMARY KEY, name VARCHAR(255), age INT, class VARCHAR(10))")
# my_cursor.execute("CREATE TABLE IF NOT EXISTS student_marks (marks_id INT PRIMARY KEY, student_id INT, science_marks VARCHAR(50), math_marks INT, FOREIGN KEY (student_id) REFERENCES student(student_id))")
# my_db.commit()
# std_data=[
#     (201, "Alice Smith", 15, "10A"),
#     (202, "Bob Johnson", 16, "10B"),
#     (203, "Charlie Brown", 15, "10A"),
#     (204, "David Wilson", 17, "11A"),
#     (205, "Eva Davis", 16, "11B")
# ]

# sql_query="INSERT INTO student (student_id, name, age, class) VALUES (%s, %s, %s, %s)"
# my_cursor.executemany(sql_query, std_data)
# my_db.commit()

# my_cursor.execute("ALTER TABLE student_marks MODIFY COLUMN science_marks INT")
# marks_data=[
#     (1, 201, 85, 90),
#     (2, 202, 90, 85),
#     (3, 203, 78, 92),
#     (4, 204, 92, 88),
#     (5, 205, 88, 95)
# ]

# sql_query="INSERT INTO student_marks (marks_id, student_id, science_marks, math_marks) VALUES (%s, %s, %s, %s)"
# my_cursor.executemany(sql_query, marks_data)
# my_db.commit()

#Manually inserting a record into the student table from user input
def get_integer(prompt):
    while True:
        try:
            return int(input(prompt))
        except ValueError:
            print("Invalid input. Please enter a whole number.")


while True:
    print("\n--- Student Database Menu ---")
    print("1. Add a student")
    print("2. Add student marks")
    print("3. Exit")

    choice = input("Choose an option (1-3): ").strip()

    if choice == "1":
        student_id = get_integer("Enter student ID: ")
        name = input("Enter student name: ").strip()
        age = get_integer("Enter student age: ")
        class_name = input("Enter student class: ").strip()

        sql_query = """
            INSERT INTO student (student_id, name, age, class)
            VALUES (%s, %s, %s, %s)
        """

        try:
            my_cursor.execute(
                sql_query,
                (student_id, name, age, class_name)
            )
            my_db.commit()
            print("Student added successfully.")
        except mysql.connector.Error as err:
            my_db.rollback()
            print(f"Could not add student: {err}")

    elif choice == "2":
        marks_id = get_integer("Enter marks ID: ")
        student_id = get_integer("Enter the student's ID: ")
        science_marks = get_integer("Enter science marks: ")
        math_marks = get_integer("Enter maths marks: ")

        sql_query = """
            INSERT INTO student_marks
            (marks_id, student_id, science_marks, math_marks)
            VALUES (%s, %s, %s, %s)
        """

        try:
            my_cursor.execute(
                sql_query,
                (marks_id, student_id, science_marks, math_marks)
            )
            my_db.commit()
            print("Student marks added successfully.")
        except mysql.connector.Error as err:
            my_db.rollback()
            print(f"Could not add marks: {err}")
            print("Make sure the student ID already exists in the student table.")

    elif choice == "3":
        print("Goodbye!")
        break

    else:
        print("Invalid choice. Please select 1, 2, or 3.")

my_cursor.close()
my_db.close()