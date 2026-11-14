"""Paging a result, with the aux dict torguapi_make_links_and_meta takes"""


class EmptyResult(Exception):
    """There are no items"""


class PageOutOfRange(Exception):
    """The page is past the last one"""


def page(items, page_number, page_size):
    """
    One page of a sequence (page_number from 1).

    Returns:
        the page's items, and {"page_number", "page_size", "record_count"}

    Raises:
        EmptyResult ("No matching records"), PageOutOfRange ("Page number
        <n> not found")
    """
    record_count = len(items)
    if record_count == 0:
        raise EmptyResult("No matching records")
    page_count = (record_count - 1) // page_size + 1
    if page_number > page_count:
        raise PageOutOfRange(f"Page number {page_number} not found")
    start = (page_number - 1) * page_size
    aux = {
        "page_number": page_number,
        "page_size": page_size,
        "record_count": record_count,
    }
    return items[start : start + page_size], aux
