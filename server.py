import logging
import os
import time
from flask import Flask, request, jsonify

app = Flask(__name__)

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
os.makedirs(LOGS_DIR, exist_ok=True)

class RequestFormatter(logging.Formatter):

    def __init__(self):
        super().__init__()

    def format(self, record):
        import datetime
        now = datetime.datetime.now()
        date_str = now.strftime('%d-%m-%Y %H:%M:%S.') + f'{now.microsecond // 1000:03d}'
        level = record.levelname
        message = record.getMessage()
        req_num = getattr(record, 'request_number', 0)
        return f"{date_str} {level}: {message} | request #{req_num}"


def setup_logger(name, log_file, level=logging.INFO, console=False):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    formatter = RequestFormatter()

    file_handler = logging.FileHandler(os.path.join(LOGS_DIR, log_file))
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    return logger


request_logger = setup_logger('request-logger', 'requests.log', logging.INFO, console=True)
books_logger   = setup_logger('books-logger',   'books.log',    logging.INFO, console=False)

LOGGERS = {
    'request-logger': request_logger,
    'books-logger':   books_logger,
}


request_counter = 0


def log(logger, level, message):
    """Emit a log line carrying the current request_number as extra."""
    extra = {'request_number': request_counter}
    logger.log(level, message, extra=extra)


books_db = {}
next_id  = 1

VALID_GENRES = ["SCI_FI", "NOVEL", "HISTORY", "MANGA", "ROMANCE", "PROFESSIONAL"]


@app.before_request
def before_request():
    global request_counter
    request_counter += 1
    request.start_time   = time.time()
    request.req_number   = request_counter

    resource = request.path
    verb     = request.method
    log(request_logger, logging.INFO,
        f"Incoming request | #{request_counter} | resource: {resource} | HTTP Verb {verb}")


@app.after_request
def after_request(response):
    duration_ms = int((time.time() - request.start_time) * 1000)
    log(request_logger, logging.DEBUG,
        f"request #{request.req_number} duration: {duration_ms}ms")
    return response


def get_and_validate_query_params():
    query_params  = request.args
    author_filter = query_params.get('author')
    price_bigger  = query_params.get('price-bigger-than')
    price_less    = query_params.get('price-less-than')
    year_bigger   = query_params.get('year-bigger-than')
    year_less     = query_params.get('year-less-than')
    genres_filter = query_params.get('genres')

    for val in [price_bigger, price_less, year_bigger, year_less]:
        if val is not None and not val.isdigit():
            raise ValueError("Invalid numeric data")

    if genres_filter:
        for genre in genres_filter.split(','):
            if genre not in VALID_GENRES:
                raise ValueError("Invalid genre")

    return {
        "author":      author_filter,
        "price_bigger": price_bigger,
        "price_less":   price_less,
        "year_bigger":  year_bigger,
        "year_less":    year_less,
        "genres":       genres_filter,
    }


def apply_book_filters(all_books, filters):
    filtered_books = list(all_books)

    if filters["author"]:
        filtered_books = [b for b in filtered_books
                          if b['author'].lower() == filters["author"].lower()]

    if filters["price_bigger"]:
        filtered_books = [b for b in filtered_books
                          if b['price'] >= int(filters["price_bigger"])]

    if filters["price_less"]:
        filtered_books = [b for b in filtered_books
                          if b['price'] <= int(filters["price_less"])]

    if filters["year_bigger"]:
        filtered_books = [b for b in filtered_books
                          if b['year'] >= int(filters["year_bigger"])]

    if filters["year_less"]:
        filtered_books = [b for b in filtered_books
                          if b['year'] <= int(filters["year_less"])]

    if filters["genres"]:
        genres_list    = filters["genres"].split(',')
        filtered_books = [b for b in filtered_books
                          if any(g in genres_list for g in b['genres'])]

    return filtered_books



@app.route('/books/health', methods=['GET'])
def health_check():
    return 'OK', 200


# 1. Create Book  (POST /book)
@app.route('/book', methods=['POST'])
def create_new_book():
    global next_id

    body   = request.get_json()
    title  = body.get('title', '')
    author = body.get('author', '')
    year   = body.get('year', 0)
    price  = body.get('price', 0)
    genres = body.get('genres', [])

    for book in books_db.values():
        if book['title'].lower() == title.lower():
            error_msg = f"Error: Book with the title [{title}] already exists in the system"
            log(books_logger, logging.ERROR, error_msg)
            return jsonify({"errorMessage": error_msg}), 409

    if year < 1940 or year > 2100:
        error_msg = (f"Error: Can't create new Book that its year [{year}] "
                     f"is not in the accepted range [1940 -> 2100]")
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 409

    if price <= 0:
        error_msg = "Error: Can't create new Book with negative price"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 409

    #INFO first, then DEBUG
    log(books_logger, logging.INFO,
        f"Creating new Book with Title [{title}]")
    log(books_logger, logging.DEBUG,
        f"Currently there are {len(books_db)} Books in the system. "
        f"New Book will be assigned with id {next_id}")

    new_book = {
        "id":     next_id,
        "title":  title,
        "author": author,
        "year":   year,
        "price":  price,
        "genres": genres,
    }
    books_db[next_id] = new_book
    new_id = next_id
    next_id += 1

    return jsonify({"result": new_id}), 200


@app.route('/books/total', methods=['GET'])
def books_total():
    try:
        filters = get_and_validate_query_params()
    except ValueError:
        return "", 400

    filtered_books = apply_book_filters(books_db.values(), filters)

    log(books_logger, logging.INFO,
        f"Total Books found for requested filters is {len(filtered_books)}")

    return jsonify({"result": len(filtered_books)}), 200


@app.route('/books', methods=['GET'])
def books_data():
    try:
        filters = get_and_validate_query_params()
    except ValueError:
        return "", 400

    filtered_books = apply_book_filters(books_db.values(), filters)
    filtered_books.sort(key=lambda b: b['title'].lower())

    log(books_logger, logging.INFO,
        f"Total Books found for requested filters is {len(filtered_books)}")

    return jsonify({"result": filtered_books}), 200



@app.route('/book', methods=['GET'])
def single_book_data():
    book_id_str = request.args.get('id')

    if not book_id_str or not book_id_str.isdigit():
        error_msg = f"Error: no such Book with id {book_id_str}"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 404

    book_id = int(book_id_str)

    if book_id not in books_db:
        error_msg = f"Error: no such Book with id {book_id}"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 404

    log(books_logger, logging.DEBUG,
        f"Fetching book id {book_id} details")

    return jsonify({"result": books_db[book_id]}), 200



@app.route('/book', methods=['PUT'])
def update_book_price():
    book_id_str = request.args.get('id')
    price_str   = request.args.get('price')

    if not book_id_str or not book_id_str.isdigit():
        error_msg = f"Error: no such Book with id {book_id_str}"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 404

    book_id = int(book_id_str)

    if book_id not in books_db:
        error_msg = f"Error: no such Book with id {book_id}"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 404

    if not price_str or not price_str.isdigit() or int(price_str) <= 0:
        error_msg = f"Error: price update for Book [{book_id_str}] must be a positive integer"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 409

    price     = int(price_str)
    old_price = books_db[book_id]['price']
    book_title = books_db[book_id]['title']

    #INFO first, then DEBUG
    log(books_logger, logging.INFO,
        f"Update Book id [{book_id}] price to {price}")
    log(books_logger, logging.DEBUG,
        f"Book [{book_title}] price change: {old_price} --> {price}")

    books_db[book_id]['price'] = price

    return jsonify({"result": old_price}), 200


@app.route('/book', methods=['DELETE'])
def delete_book():
    book_id_str = request.args.get('id')

    if not book_id_str or not book_id_str.isdigit():
        error_msg = f"Error: no such book with id {book_id_str}"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 404

    book_id = int(book_id_str)

    if book_id not in books_db:
        error_msg = f"Error: no such book with id {book_id}"
        log(books_logger, logging.ERROR, error_msg)
        return jsonify({"errorMessage": error_msg}), 404

    book_title = books_db[book_id]['title']

    #INFO first
    log(books_logger, logging.INFO,
        f"Removing book [{book_title}]")

    del books_db[book_id]
    books_left = len(books_db)

    #DEBUG after deletion
    log(books_logger, logging.DEBUG,
        f"After removing book [{book_title}] id: [{book_id}] "
        f"there are {books_left} books in the system")

    return jsonify({"result": books_left}), 200



VALID_LEVELS = {'ERROR': logging.ERROR, 'INFO': logging.INFO, 'DEBUG': logging.DEBUG}


@app.route('/logs/level', methods=['GET'])
def get_log_level():
    logger_name = request.args.get('logger-name')

    if not logger_name or logger_name not in LOGGERS:
        return f"Error: unknown logger '{logger_name}'", 400

    level_name = logging.getLevelName(LOGGERS[logger_name].level)
    return level_name, 200


@app.route('/logs/level', methods=['PUT'])
def set_log_level():
    logger_name  = request.args.get('logger-name')
    logger_level = request.args.get('logger-level', '').upper()

    if not logger_name or logger_name not in LOGGERS:
        return f"Error: unknown logger '{logger_name}'", 400

    if logger_level not in VALID_LEVELS:
        return f"Error: unknown level '{logger_level}'", 400

    LOGGERS[logger_name].setLevel(VALID_LEVELS[logger_level])

    for handler in LOGGERS[logger_name].handlers:
        handler.setLevel(VALID_LEVELS[logger_level])

    return logger_level, 200



if __name__ == '__main__':
    app.run(port=8574)
