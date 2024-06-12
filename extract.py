import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from langchain.prompts import SystemMessagePromptTemplate, ChatPromptTemplate, \
    HumanMessagePromptTemplate
from langchain_openai import ChatOpenAI
from langchain.output_parsers import PydanticOutputParser
from pydantic import BaseModel, Field
from llmwhisperer.client import LLMWhispererClient


class CustomerAddress(BaseModel):
    zip_code: str = Field(description="Should contain the zip code alone")
    city: str = Field(description="Should hold the city name from the address")
    full_address: str = Field(description="Should hold the full address of the customer")


class PaymentInfo(BaseModel):
    due_date: datetime = Field(description="The due date of the credit card statement. Also known as the payment due "
                                           "date")
    minimum_payment: float = Field(description="the minimum amount that is due")
    new_balance: float = Field(description="the total new balance amount that can be paid")


class SpendLineItem(BaseModel):
    spend_date: datetime = Field(description="The date of the transaction. If the year part isn't mentioned in the "
                                             "line item explicitly, pick up the year from the statement date and use "
                                             "it instead.")
    spend_description: str = Field(description="The description of the spend")
    amount: float = Field(description="The amount of the transaction")


class ParsedCreditCardStatement(BaseModel):
    issuer_name: str = Field(description="What is the name of the issuer or the bank who has issued this credit card? "
                                         "I am not interested in the legal entity, but the primary brand name of the "
                                         "credit card.")
    customer_name: str = Field(description="What is the name of the customer to whom this credit card statement "
                                           "belongs to? Format the name of the customer well with the first letter of "
                                           "each name capitalized.")
    customer_address: CustomerAddress = Field(description="Since there might be multiple addresses in the context "
                                                          "provided to you, first gather all addresses. Try to "
                                                          "understand whom this credit card statement is being "
                                                          "addressed to or in other words, the name of the customer. "
                                                          "Find the address that matches that person's. Be sure to "
                                                          "return the customer's address, for whom this credit card "
                                                          "statement is for. Do not respond with any other address.")
    payment_info: PaymentInfo = Field(description="Payment information is important part of any credit card statement "
                                                  "and it consists of the new balance or the full amount due for the "
                                                  "current statement, the minimum payment due and the payment due "
                                                  "date.")
    spend_line_items: list[SpendLineItem] = Field(description="This credit card statement contains spending details "
                                                              "line items. Spend details can be split across the "
                                                              "provided context. Respond with details of all the "
                                                              "spend items by looking at the whole context always.")


def make_llm_whisperer_call(file_path):
    print(f"Processing file:{file_path}...")
    # with open(file_path, "rb") as f:
    #     data = f.read()
    #
    # headers = {
    #     'Content-Type': 'application/octet-stream',
    #     'unstract-key': os.getenv('UNSTRACT_LLMWHISPERER_KEY')
    # }
    # url = 'https://llmwhisperer-api.unstract.com/v1/whisper?processing_mode=ocr&output_mode=line-printer'
    # return requests.post(url, headers=headers, data=data)
    # LLMWhisperer API key is picked up from the environment variable
    client = LLMWhispererClient()
    result = client.whisper(file_path=file_path, processing_mode="ocr", output_mode="line-printer")
    return result["extracted_text"]


def generate_cache_file_name(file_path):
    # For our use case, PDFs won't be less than 4096, practically speaking.
    if os.path.getsize(file_path) < 4096:
        error_exit("File too small to process.")
    with open(file_path, "rb") as f:
        first_block = f.read(4096)
        # seek to the last block
        f.seek(-4096, os.SEEK_END)
        f.read(4096)
        last_block = f.read(4096)

    first_md5_hash = hashlib.md5(first_block).hexdigest()
    last_md5_hash = hashlib.md5(last_block).hexdigest()
    return f"/tmp/{first_md5_hash}_{last_md5_hash}.txt"


def is_file_cached(file_path):
    cache_file_name = generate_cache_file_name(file_path)
    cache_file = Path(cache_file_name)
    if cache_file.is_file():
        return True
    else:
        return False


def extract_text(file_path):
    if is_file_cached(file_path):
        print(f"Info: File {file_path} is already cached.")
        cache_file_name = generate_cache_file_name(file_path)
        with open(cache_file_name, "r") as f:
            return f.read()
    else:
        data = make_llm_whisperer_call(file_path)
        cache_file_name = generate_cache_file_name(file_path)
        with open(cache_file_name, "w") as f:
            f.write(data)
        return data


def error_exit(error_message):
    print(error_message)
    sys.exit(1)


def show_usage_and_exit():
    error_exit("Please pass name of directory or file to process.")


def enumerate_pdf_files(file_path):
    files_to_process = []
    # Users can pass a directory or a file name
    if os.path.isfile(file_path):
        if os.path.splitext(file_path)[1][1:].strip().lower() == 'pdf':
            files_to_process.append(file_path)
    elif os.path.isdir(file_path):
        files = os.listdir(file_path)
        for file_name in files:
            full_file_path = os.path.join(file_path, file_name)
            if os.path.isfile(full_file_path):
                if os.path.splitext(file_name)[1][1:].strip().lower() == 'pdf':
                    files_to_process.append(full_file_path)
    else:
        error_exit(f"Error. {file_path} should be a file or a directory.")

    return files_to_process


def extract_values_from_file(raw_file_data):
    preamble = ("\n"
                "Your ability to extract and summarize this information accurately is essential for effective "
                "credit card statement analysis. Pay close attention to the credit card statement's language, "
                "structure, and any cross-references to ensure a comprehensive and precise extraction of "
                "information. Do not use prior knowledge or information from outside the context to answer the "
                "questions. Only use the information provided in the context to answer the questions.\n")
    postamble = "Do not include any explanation in the reply. Only include the extracted information in the reply."
    system_template = "{preamble}"
    system_message_prompt = SystemMessagePromptTemplate.from_template(system_template)
    human_template = "{format_instructions}\n{raw_file_data}\n{postamble}"
    human_message_prompt = HumanMessagePromptTemplate.from_template(human_template)

    parser = PydanticOutputParser(pydantic_object=ParsedCreditCardStatement)
    print(parser.get_format_instructions())

    # compile chat template
    chat_prompt = ChatPromptTemplate.from_messages([system_message_prompt, human_message_prompt])
    request = chat_prompt.format_prompt(preamble=preamble,
                                        format_instructions=parser.get_format_instructions(),
                                        raw_file_data=raw_file_data,
                                        postamble=postamble).to_messages()
    model = ChatOpenAI()
    print("Querying model...")
    result = model(request, temperature=0)
    print("Response from model:")
    print(result.content)
    return result.content


def process_pdf_files(file_list):
    for file_path in file_list:
        raw_file_data = extract_text(file_path)
        print(f"Extracted text for file {file_path}:\n{raw_file_data}")
        extracted_json = extract_values_from_file(raw_file_data)
        json_file_path = f"{file_path}.json"
        with open(json_file_path, "w") as f:
            f.write(extracted_json)


def main():
    load_dotenv()
    if len(sys.argv) < 2:
        show_usage_and_exit()

    print(f"Processing path {sys.argv[1]}...")
    file_list = enumerate_pdf_files(sys.argv[1])
    print(f"Processing {len(file_list)} files...")
    print(f"Processing first file: {file_list[0]}...")
    process_pdf_files(file_list)


if __name__ == '__main__':
    main()
