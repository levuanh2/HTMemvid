# QUY TRÌNH HỆ THỐNG TẠO SƠ ĐỒ TƯ DUY

## I. TỔNG QUAN: SƠ ĐỒ TƯ DUY TRONG HỆ THỐNG DÙNG ĐỂ LÀM GÌ?

Hệ thống tạo ra hai loại sơ đồ tư duy với mục đích khác nhau:

**Memory Tree (Cây Trí nhớ)**: Được tạo tự động sau khi người dùng tải tài liệu lên. Đây là cấu trúc cây phản ánh logic của tài liệu, giúp hệ thống hiểu được cấu trúc và mối quan hệ giữa các phần để trả lời câu hỏi có ngữ cảnh sâu hơn.

**Mind Map (Sơ đồ Tư duy)**: Được tạo theo yêu cầu của người dùng khi họ muốn xem tổng quan kiến thức. Đây là sơ đồ trực quan giúp người học dễ ghi nhớ và nắm bắt cấu trúc kiến thức.

Cả hai loại đều được xây dựng từ cùng một nguồn dữ liệu ban đầu nhưng qua các quy trình xử lý khác nhau để phục vụ mục đích riêng.

---

## II. NGUỒN DỮ LIỆU ĐẦU VÀO

### Dữ liệu thô đến từ đâu?

Khi người dùng tải lên một tài liệu (PDF, DOCX, TXT, hoặc hình ảnh), hệ thống nhận được file đó và lưu tạm thời trong thư mục input.

### Định dạng ban đầu là gì?

Dữ liệu ban đầu là các file ở định dạng khác nhau:
- File PDF: chứa văn bản và có thể có hình ảnh, bảng biểu
- File DOCX: tài liệu Word với các đoạn văn, bảng, danh sách
- File TXT: văn bản thuần túy
- Hình ảnh: chứa văn bản được scan hoặc chụp

Tất cả các định dạng này đều được chuyển đổi thành một chuỗi văn bản thô để hệ thống có thể xử lý tiếp.

---

## III. CÁC BƯỚC XỬ LÝ CHÍNH

### BƯỚC 1: CHUẨN BỊ DỮ LIỆU - Trích xuất và Chuẩn hóa Nội dung

**Input**: File tài liệu ở các định dạng khác nhau (PDF, DOCX, TXT, hình ảnh)

**Hệ thống làm gì**:
- Đọc nội dung từ file PDF: lấy văn bản từ tất cả các trang, giữ nguyên thứ tự
- Đọc nội dung từ file DOCX: lấy văn bản từ các đoạn văn, bảo toàn cấu trúc cơ bản
- Đọc nội dung từ file TXT: đọc trực tiếp văn bản
- Xử lý hình ảnh: sử dụng công nghệ nhận dạng ký tự (OCR) để trích xuất văn bản từ hình ảnh, hỗ trợ cả tiếng Việt và tiếng Anh

**Output**: Một chuỗi văn bản thô, đã được chuẩn hóa và loại bỏ các ký tự nhiễu không cần thiết

**Output này được dùng cho**: Bước 2 - Chia nhỏ và chuẩn hóa nội dung

---

### BƯỚC 2: CHIA NHỎ VÀ CHUẨN HÓA NỘI DUNG - Phân đoạn Ngữ nghĩa

**Input**: Chuỗi văn bản thô từ bước 1

**Hệ thống làm gì**:
Hệ thống không chia văn bản theo kích thước cố định (ví dụ cứ 1000 ký tự một đoạn), mà phân tích ý nghĩa để tìm các điểm chia tự nhiên.

Quy trình như sau:
1. Chuyển đổi các câu thành các vector số học đại diện cho ý nghĩa của câu
2. So sánh độ tương đồng về ý nghĩa giữa các câu liên tiếp
3. Khi phát hiện sự thay đổi về chủ đề hoặc ý nghĩa (độ tương đồng giảm xuống dưới ngưỡng), hệ thống xác định đó là điểm chia
4. Tạo ra các đoạn văn (chunk) với kích thước tối thiểu để đảm bảo mỗi đoạn chứa đủ thông tin

**Tại sao cách này tốt hơn chia theo kích thước cố định?**
- Mỗi đoạn là một đơn vị tri thức hoàn chỉnh về mặt ngữ nghĩa
- Không bị cắt ngang giữa các ý tưởng
- Khi tìm kiếm sau này, hệ thống có thể tìm được các đoạn liên quan một cách chính xác hơn

**Output**: Danh sách các đoạn văn (chunks), mỗi đoạn là một đơn vị tri thức có nghĩa hoàn chỉnh

**Output này được dùng cho**: 
- Bước 3 - Tóm tắt và nhóm nội dung (để tạo Memory Tree)
- Tạo Mind Map theo yêu cầu người dùng
- Lưu trữ vào hệ thống tìm kiếm để truy vấn sau này

---

### BƯỚC 3: TÓM TẮT VÀ NHÓM NỘI DUNG - Xây dựng Cấu trúc Cây Cơ bản

Đây là bước chỉ áp dụng cho việc tạo **Memory Tree** (không áp dụng cho Mind Map theo yêu cầu).

**Input**: Danh sách các đoạn văn (chunks) từ bước 2

**Hệ thống làm gì**:

**3.1. Nhóm các đoạn thành các phần (Sections)**:
- Phân tích các đoạn và nhóm chúng lại dựa trên vị trí và thứ tự trong tài liệu
- Nếu tài liệu có ít đoạn (dưới 18 đoạn), tất cả được nhóm thành một phần duy nhất
- Nếu tài liệu có nhiều đoạn, hệ thống chia đều thành khoảng 6 phần, mỗi phần chứa ít nhất 3 đoạn

**3.2. Tạo tóm tắt cho từng phần**:
- Với mỗi phần đã nhóm, hệ thống lấy nội dung của tất cả các đoạn trong phần đó
- Gửi nội dung này đến hệ thống AI để tạo tóm tắt ngắn gọn (5-10 câu) nêu lên ý chính
- Tóm tắt này được viết theo cách dễ hiểu, tập trung vào mục tiêu, khái niệm và giải pháp chính

**3.3. Tạo tóm tắt cho toàn bộ tài liệu**:
- Lấy nội dung của tất cả các đoạn (hoặc một phần lớn nếu tài liệu quá dài)
- Gửi đến hệ thống AI để tạo tóm tắt tổng quan về toàn bộ tài liệu
- Tóm tắt này đại diện cho nội dung chính của cả tài liệu

**3.4. Phân loại mục đích**:
- Với mỗi phần và tài liệu, hệ thống phân tích xem nội dung thuộc loại nào:
  - Định nghĩa: giải thích khái niệm
  - Quy trình: mô tả các bước, cách làm
  - Lập luận: phân tích, đưa ra quan điểm
  - So sánh: đối chiếu các khái niệm
  - Tham khảo: liên kết đến nội dung khác

**Output**: 
- Một cấu trúc cây với:
  - Node gốc (Document Node): đại diện cho toàn bộ tài liệu, chứa tóm tắt tổng quan
  - Các node con (Section Nodes): đại diện cho từng phần, mỗi node chứa tóm tắt của phần đó
  - Mối quan hệ cha-con giữa Document Node và các Section Nodes

**Output này được dùng cho**: Bước 4 - Hình thành cấu trúc cây hoàn chỉnh

---

### BƯỚC 4: HÌNH THÀNH CẤU TRÚC CÂY - Hoàn thiện Memory Tree

**Input**: Các node đã được tạo ở bước 3 (Document Node và Section Nodes)

**Hệ thống làm gì**:

**4.1. Tạo biểu diễn số học cho mỗi node**:
- Mỗi tóm tắt (của document và các section) được chuyển đổi thành một vector số học đa chiều
- Vector này đại diện cho ý nghĩa của nội dung, cho phép so sánh và tìm kiếm dựa trên ngữ nghĩa

**4.2. Liên kết các node với các đoạn văn gốc**:
- Mỗi node lưu lại danh sách các đoạn văn (chunks) mà nó đại diện
- Điều này cho phép khi tìm kiếm ở mức node, hệ thống có thể truy cập đến các đoạn văn chi tiết bên dưới

**4.3. Xây dựng chỉ mục tìm kiếm**:
- Tất cả các vector của các node được lưu vào một chỉ mục đặc biệt
- Chỉ mục này cho phép tìm kiếm nhanh các node có nội dung liên quan đến câu hỏi của người dùng

**4.4. Lưu trữ cấu trúc cây**:
- Cấu trúc cây được lưu vào file JSON, bao gồm:
  - Thông tin về tài liệu (tên, thời điểm tạo)
  - Danh sách tất cả các node với đầy đủ thông tin (tiêu đề, tóm tắt, phân loại mục đích, liên kết đến chunks)
  - Trạng thái xây dựng (đang xây dựng hoặc đã hoàn thành)

**Output**: Memory Tree hoàn chỉnh được lưu trong hệ thống, có thể được sử dụng để:
- Truy vấn ở mức độ cao hơn (document/section level) thay vì chỉ ở mức đoạn văn
- Hiểu ngữ cảnh và mối quan hệ giữa các phần khi trả lời câu hỏi

**Output này được dùng cho**: Hệ thống truy vấn và trả lời câu hỏi

---

### BƯỚC 5: HOÀN THIỆN SƠ ĐỒ TƯ DUY - Tạo Mind Map theo Yêu cầu

Đây là bước riêng biệt, chỉ chạy khi người dùng yêu cầu tạo Mind Map (không tự động như Memory Tree).

**Input**: 
- Danh sách các đoạn văn (chunks) từ các tài liệu đã được chọn
- Người dùng có thể chọn một hoặc nhiều tài liệu để tạo Mind Map tổng hợp

**Hệ thống làm gì**:

**5.1. Chuẩn bị dữ liệu**:
- Lấy tất cả các đoạn văn từ các tài liệu đã chọn
- Nếu một đoạn văn đã được chia nhỏ thành các sub-chunk (do quá dài), hệ thống tự động ghép lại thành đoạn văn gốc hoàn chỉnh
- Loại bỏ các đoạn trùng lặp hoặc không có nội dung

**5.2. Phát hiện và loại bỏ thông tin không cần thiết**:
- Phân tích các đoạn văn để tìm các cụm từ biểu thị thông tin hành chính (ví dụ: tên giảng viên, điểm số, ngày tháng, địa điểm)
- Loại bỏ các đoạn chứa chủ yếu thông tin hành chính này, chỉ giữ lại nội dung học thuật

**5.3. Tạo sơ đồ tư duy** (có nhiều chiến lược):

**Chiến lược Iterative (Lặp lại từng bước)**:
1. Xác định chủ đề trung tâm (root): Phân tích các đoạn văn để tìm chủ đề học thuật chính, loại bỏ các tiêu đề hành chính
2. Mở rộng từng nhánh: Bắt đầu từ chủ đề trung tâm, lần lượt mở rộng từng nhánh bằng cách:
   - Phân tích các đoạn văn liên quan đến nhánh đó
   - Xác định các chủ đề con cụ thể
   - Tạo các nhánh con với tên ngắn gọn (2-8 từ)
   - Quyết định có tiếp tục mở rộng nhánh con hay không dựa trên nội dung còn lại
3. Dừng khi: Không còn nội dung phù hợp để mở rộng, hoặc đã đạt độ sâu phù hợp

**Chiến lược CMGN (Coreference-Guided)**:
1. Trích xuất các câu từ các đoạn văn
2. Xây dựng đồ thị tham chiếu: Phân tích các câu để tìm:
   - Các thực thể được nhắc lại nhiều lần (ví dụ: cùng một khái niệm xuất hiện ở nhiều câu)
   - Mối quan hệ giữa các câu (ví dụ: câu này giải thích câu kia, câu này là nguyên nhân của câu kia)
3. Nhóm các câu theo thực thể và quan hệ: Tạo các cụm câu cùng thực thể, xác định các câu quan trọng nhất
4. Xây dựng sơ đồ tư duy từ đồ thị: Sử dụng đồ thị đã xây dựng để tạo cấu trúc cây logic, các nhánh được nhóm theo thực thể và quan hệ

**5.4. Rà soát và cải thiện chất lượng** (Áp dụng cho cả hai chiến lược):

**Rà soát tính chính xác (Factuality Critic)**:
- Kiểm tra xem mỗi nhánh trong sơ đồ có được hỗ trợ bởi ít nhất một đoạn văn trong tài liệu không
- Loại bỏ các nhánh không có bằng chứng trong tài liệu
- Thêm thông tin trích dẫn vào các nhánh lá để người đọc biết nhánh đó dựa trên đoạn nào

**Rà soát cấu trúc cục bộ (Local Structure Critic)**:
- Đảm bảo mỗi nhánh lá kết thúc ở một khái niệm cụ thể, không trùng lặp với tiêu đề cha
- Nếu một nhánh lá quá chung chung, đổi tên cho cụ thể hơn hoặc hợp nhất vào nhánh khác
- Thêm lớp con mới nếu cần để đạt đến ý cụ thể

**Rà soát cấu trúc tổng thể (Global Structure Critic)**:
- Kiểm tra số lượng nhánh cấp 1 (thường khoảng 4-7 nhánh)
- Đảm bảo phân nhóm logic và cân đối số nhánh con giữa các nhánh chính
- Hợp nhất các nhánh riêng lẻ yếu (ít con, trùng chủ đề) vào nhánh phù hợp hơn
- Tối ưu cấu trúc để giống như một mục lục cân đối

**5.5. Chuyển đổi thành định dạng phẳng**:
- Chuyển cấu trúc cây lồng nhau thành danh sách các node phẳng
- Mỗi node có: ID, tiêu đề, node cha (parent), và có thể có mô tả ngắn (detail)

**Output**: 
- Sơ đồ tư duy hoàn chỉnh dưới dạng danh sách các node phẳng
- Mỗi node có thông tin về vị trí trong cây (parent-child relationship)
- Sơ đồ được lưu lại để người dùng có thể xem lại sau

**Output này được dùng cho**: Hiển thị cho người dùng dưới dạng sơ đồ trực quan, giúp học tập và ôn tập

---

## IV. DỮ LIỆU LƯU TRỮ CUỐI CÙNG

### Memory Tree được lưu ở đâu?

Memory Tree được lưu trong file `memory/memory_trees.json`. Mỗi tài liệu có một entry riêng trong file này.

**Gồm những thành phần nào?**
- **Thông tin tài liệu**: Tên tài liệu (source_stem), thời điểm tạo (built_at), phiên bản (version), trạng thái (status: building hoặc completed)
- **Danh sách các node**:
  - **Document Node**: 
    - ID duy nhất
    - Tiêu đề (thường là tên tài liệu)
    - Tóm tắt tổng quan về tài liệu
    - Vector số học đại diện cho ý nghĩa
    - Danh sách các đoạn văn (chunks) mà nó đại diện
    - Danh sách các node con (section nodes)
    - Phân loại mục đích (definition, procedure, argument, comparison, reference)
  - **Section Nodes**:
    - ID duy nhất
    - Tiêu đề của section
    - Tóm tắt nội dung section
    - Vector số học đại diện cho ý nghĩa
    - Danh sách các đoạn văn (chunks) thuộc section đó
    - Phân loại mục đích

**Chỉ mục tìm kiếm**: 
- File `memory/memory_index.faiss`: Chứa các vector số học của tất cả các node để tìm kiếm nhanh
- File `memory/memory_index.json`: Chứa metadata liên kết giữa vector và node (ID node, loại node, tiêu đề, nguồn tài liệu)

### Mind Map được lưu ở đâu?

Mind Map được lưu trong file `memory/mindmaps.json`. Mỗi Mind Map được tạo theo yêu cầu có một entry riêng.

**Gồm những thành phần nào?**
- **Thông tin Mind Map**: ID duy nhất, tiêu đề, danh sách tài liệu nguồn, thời điểm tạo, chiến lược đã sử dụng (iterative, CMGN, fallback)
- **Danh sách các node phẳng**:
  - Mỗi node có: ID, node cha (parent), tiêu đề (title), và có thể có mô tả ngắn (detail)
  - Cấu trúc cây được thể hiện qua mối quan hệ parent-child giữa các node

---

## V. TÓM TẮT LUỒNG TƯ DUY CỦA HỆ THỐNG

### Đối với Memory Tree (Tự động tạo):

Hệ thống "nghĩ" như sau:

1. **Nhận tài liệu** → "Tôi cần đọc và hiểu nội dung này"

2. **Trích xuất văn bản** → "Tôi đã có nội dung thô, bây giờ cần tổ chức lại"

3. **Chia theo ngữ nghĩa** → "Tôi sẽ chia thành các đoạn có ý nghĩa hoàn chỉnh, không cắt ngang ý tưởng"

4. **Nhóm các đoạn** → "Các đoạn này có vẻ cùng một chủ đề, tôi sẽ nhóm chúng lại thành một phần"

5. **Tóm tắt từng phần** → "Mỗi phần nói về gì? Tôi sẽ tạo một bản tóm tắt ngắn gọn"

6. **Tóm tắt toàn bộ** → "Toàn bộ tài liệu nói về gì? Tôi sẽ tạo một bản tóm tắt tổng quan"

7. **Xây dựng cấu trúc cây** → "Tài liệu là gốc, các phần là nhánh con. Tôi sẽ lưu lại cấu trúc này để hiểu mối quan hệ"

8. **Tạo chỉ mục tìm kiếm** → "Bây giờ tôi có thể nhanh chóng tìm các phần liên quan đến câu hỏi của người dùng"

### Đối với Mind Map (Tạo theo yêu cầu):

Hệ thống "nghĩ" như sau:

1. **Nhận yêu cầu tạo Mind Map** → "Người dùng muốn xem tổng quan kiến thức, tôi sẽ tạo sơ đồ trực quan"

2. **Lấy các đoạn văn từ tài liệu đã chọn** → "Tôi cần tất cả nội dung từ các tài liệu này"

3. **Loại bỏ thông tin không cần thiết** → "Tôi sẽ bỏ qua các thông tin hành chính, chỉ giữ lại nội dung học thuật"

4. **Xác định chủ đề trung tâm** → "Chủ đề chính của tất cả nội dung này là gì? Tôi sẽ tìm ra ý tưởng cốt lõi"

5. **Mở rộng từng nhánh** → "Từ chủ đề chính, tôi sẽ tìm các chủ đề con cụ thể. Mỗi chủ đề con lại có các ý nhỏ hơn. Tôi sẽ tiếp tục mở rộng cho đến khi không còn nội dung phù hợp"

6. **Rà soát tính chính xác** → "Mỗi nhánh tôi tạo có được hỗ trợ bởi nội dung trong tài liệu không? Tôi sẽ kiểm tra và loại bỏ những nhánh không có bằng chứng"

7. **Rà soát cấu trúc** → "Cấu trúc này có hợp lý không? Các nhánh có cân đối không? Tôi sẽ điều chỉnh để sơ đồ dễ hiểu và dễ ghi nhớ hơn"

8. **Hoàn thiện** → "Tôi đã có một sơ đồ tư duy hoàn chỉnh, giúp người học dễ dàng nắm bắt tổng quan và mối quan hệ giữa các phần"

---

## KẾT LUẬN

Hệ thống tạo sơ đồ tư duy thông qua một quy trình nhiều bước, từ việc đọc và hiểu nội dung thô đến việc tổ chức thành cấu trúc cây logic. Mỗi bước đều có vai trò cụ thể trong việc chuyển đổi tài liệu thành tri thức có thể truy vấn và dễ hiểu.

**Memory Tree** được tạo tự động để hỗ trợ hệ thống hiểu cấu trúc và trả lời câu hỏi tốt hơn. **Mind Map** được tạo theo yêu cầu để giúp người học nắm bắt tổng quan kiến thức một cách trực quan.

Cả hai loại sơ đồ đều dựa trên cùng một nguồn dữ liệu (các đoạn văn đã được chia theo ngữ nghĩa) nhưng được xử lý theo cách khác nhau để phục vụ mục đích riêng của từng loại.


