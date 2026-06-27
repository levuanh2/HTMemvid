"""
shared/ — code dùng chung cho cả monolith (app/) và các service (services/).

Gồm:
- interfaces/  : các Protocol (seam) để inject thay vì import cứng — "liên kết dẻo".
- config.py    : Settings tập trung, nạp env một lần.
- proto/       : hợp đồng gRPC (.proto) giữa monolith và service.
- env_loader   : (sẽ chuyển vào đây ở Phase 2) nạp .env.

Phase 1: các module hiện tại CHƯA bắt buộc dùng shared/; đây là lớp hợp đồng
được thêm vào song song, không phá vỡ hành vi cũ. Các impl cụ thể (LocalLLMProvider,
FaissVectorStore, HybridRetriever...) sẽ "khớp" các Protocol này một cách structural.
"""
