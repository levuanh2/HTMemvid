# HỆ THỐNG LƯU TRỮ VÀ TRUY VẤN TRI THỨC TỪ TÀI LIỆU HỌC THUẬT DỰA TRÊN VIDEO QR CODE VÀ SƠ ĐỒ TƯ DUY TỰ ĐỘNG

## 1. GIỚI THIỆU

Trong bối cảnh số hóa giáo dục và nghiên cứu, việc lưu trữ, tổ chức và truy xuất tri thức từ tài liệu học thuật đang đối mặt với nhiều thách thức. Người học không chỉ cần đọc và hiểu nội dung mà còn cần ghi nhớ cấu trúc, mối quan hệ giữa các khái niệm, và có khả năng truy xuất thông tin một cách hiệu quả trong dài hạn. Các phương pháp lưu trữ truyền thống như file văn bản, cơ sở dữ liệu, hoặc hệ thống quản lý tài liệu thường phụ thuộc vào nền tảng cụ thể và có thể gặp khó khăn trong việc bảo toàn dữ liệu lâu dài.

Nghiên cứu này đề xuất một hệ thống phần mềm tích hợp các kỹ thuật trí tuệ nhân tạo để phân tích, tổ chức và lưu trữ tri thức từ tài liệu học thuật. Điểm nổi bật của hệ thống là việc sử dụng **Video QR Code** như một phương pháp lưu trữ tri thức mới, kết hợp với việc tự động sinh **sơ đồ tư duy (Mind Map)** và hỗ trợ **truy vấn ngữ nghĩa** thay vì tìm kiếm từ khóa truyền thống.

Hệ thống được thiết kế nhằm giải quyết các vấn đề:
- Lưu trữ tri thức bền vững, không phụ thuộc nền tảng cụ thể
- Tổ chức tri thức dưới dạng cấu trúc cây để dễ ghi nhớ và nắm bắt
- Truy vấn thông minh dựa trên ngữ nghĩa thay vì từ khóa
- Hỗ trợ học tập, nghiên cứu và ôn tập hiệu quả

## 2. CƠ SỞ VÀ ĐỘNG CƠ NGHIÊN CỨU

### 2.1. Bối cảnh và Vấn đề

Trong môi trường học thuật hiện đại, người học phải xử lý một lượng lớn tài liệu với các đặc điểm:
- **Độ dài**: Tài liệu học thuật thường có hàng chục đến hàng trăm trang, đòi hỏi thời gian đọc và phân tích đáng kể
- **Cấu trúc phức tạp**: Nội dung được tổ chức theo nhiều cấp độ (chương, mục, tiểu mục) với mối quan hệ logic phức tạp
- **Khối lượng tri thức lớn**: Mỗi tài liệu chứa nhiều khái niệm, định nghĩa, quy trình và mối liên hệ giữa chúng

Người học gặp khó khăn trong:
- **Đọc hiểu**: Việc đọc toàn bộ tài liệu dài mất nhiều thời gian và công sức
- **Ghi nhớ cấu trúc**: Khó nắm bắt tổng quan và mối quan hệ giữa các phần nội dung
- **Lưu trữ và truy xuất**: Các phương pháp lưu trữ truyền thống phụ thuộc vào nền tảng, khó bảo toàn lâu dài và chia sẻ

### 2.2. Động cơ Nghiên cứu

Nghiên cứu này được thúc đẩy bởi nhu cầu:
- **Lưu trữ tri thức bền vững**: Tìm phương pháp lưu trữ tri thức không phụ thuộc vào nền tảng cụ thể, có thể bảo toàn và truyền tải dễ dàng
- **Tổ chức tri thức hiệu quả**: Sử dụng AI để tự động phân tích và tổ chức nội dung thành cấu trúc dễ hiểu, dễ ghi nhớ
- **Truy vấn thông minh**: Vượt qua giới hạn của tìm kiếm từ khóa, cho phép tìm kiếm dựa trên ngữ nghĩa và ý nghĩa

### 2.3. Đóng góp của Nghiên cứu

Nghiên cứu này đề xuất:
1. **Mô hình lưu trữ tri thức mới dựa trên Video QR Code**: Mã hóa tri thức và metadata vào QR Code, sau đó đóng gói thành video như một "thiết bị lưu trữ tri thức" có thể chia sẻ, sao lưu và bảo toàn dài hạn
2. **Tự động sinh sơ đồ tư duy**: Sử dụng AI để phân tích nội dung và tự động xây dựng cấu trúc cây (Memory Tree) phản ánh mối quan hệ logic giữa các phần
3. **Truy vấn ngữ nghĩa**: Hỗ trợ tìm kiếm dựa trên ý nghĩa thay vì từ khóa chính xác, cho phép người dùng đặt câu hỏi tự nhiên và nhận được câu trả lời có ngữ cảnh

## 3. KIẾN TRÚC TỔNG THỂ HỆ THỐNG

### 3.1. Tổng quan Kiến trúc

Hệ thống được xây dựng theo mô hình client-server, bao gồm ba thành phần chính:

**Giao diện Người dùng (Frontend)**:
- Cho phép người dùng tải lên tài liệu ở nhiều định dạng (PDF, DOCX, TXT, hình ảnh)
- Hiển thị trạng thái xử lý và tiến trình trong thời gian thực
- Cung cấp giao diện truy vấn bằng ngôn ngữ tự nhiên
- Hiển thị sơ đồ tư duy và kết quả truy vấn một cách trực quan

**Máy chủ Xử lý (Backend)**:
- Xử lý toàn bộ pipeline từ nhận tài liệu đến lưu trữ tri thức
- Thực hiện phân tích nội dung bằng AI
- Tạo video QR Code và xây dựng sơ đồ tư duy
- Quản lý hệ thống lưu trữ và truy vấn

**Hệ thống Lưu trữ và Truy vấn Tri thức**:
- Lưu trữ video QR Code chứa tri thức đã mã hóa
- Duy trì chỉ mục vector (vector index) cho tìm kiếm ngữ nghĩa
- Quản lý cấu trúc sơ đồ tư duy (Memory Tree)
- Đảm bảo đồng bộ giữa các thành phần dữ liệu

### 3.2. Luồng Hoạt động Tổng thể

Khi người dùng tải lên một tài liệu:

1. **Nhận và Lưu trữ Tạm thời**: Tài liệu được lưu tạm thời trong hệ thống
2. **Xử lý Nền (Background Processing)**: Hệ thống bắt đầu xử lý tài liệu trong background, không chặn giao diện người dùng
3. **Theo dõi Tiến trình**: Người dùng có thể theo dõi tiến trình xử lý qua giao diện
4. **Sử dụng Sớm**: Người dùng có thể bắt đầu truy vấn ngay khi một phần dữ liệu đã sẵn sàng
5. **Hoàn thiện Dần**: Hệ thống tiếp tục xây dựng sơ đồ tư duy và cải thiện chất lượng truy vấn ở background

## 4. PIPELINE XỬ LÝ VÀ LƯU TRỮ VIDEO QR CODE

### 4.1. Tổng quan Pipeline

Pipeline xử lý tài liệu được thực hiện qua nhiều giai đoạn tuần tự, từ trích xuất nội dung thô đến lưu trữ tri thức dưới dạng video QR Code. Mỗi giai đoạn có vai trò cụ thể trong việc chuyển đổi tài liệu thành tri thức có thể truy vấn và lưu trữ bền vững.

### 4.2. Trích xuất Nội dung (Content Extraction)

Giai đoạn đầu tiên là trích xuất nội dung văn bản từ tài liệu ở nhiều định dạng khác nhau:

**Xử lý PDF**:
- Sử dụng thư viện xử lý PDF để đọc văn bản từ các trang
- Giữ nguyên thứ tự và cấu trúc nội dung
- Xử lý các trường hợp PDF có hình ảnh hoặc bảng biểu

**Xử lý DOCX**:
- Đọc nội dung từ các đoạn văn (paragraph) trong tài liệu Word
- Bảo toàn cấu trúc định dạng cơ bản
- Xử lý các phần tử đặc biệt như bảng, danh sách

**Xử lý TXT**:
- Đọc trực tiếp từ file văn bản thuần túy
- Xử lý encoding đa dạng (UTF-8, các encoding khác)

**Xử lý Hình ảnh (OCR)**:
- Sử dụng công nghệ nhận dạng ký tự quang học (Optical Character Recognition)
- Hỗ trợ nhận dạng văn bản tiếng Việt và tiếng Anh
- Xử lý các hình ảnh chứa văn bản từ tài liệu được scan

Kết quả của giai đoạn này là một chuỗi văn bản thô, đã được chuẩn hóa và loại bỏ các ký tự nhiễu không cần thiết.

### 4.3. Phân đoạn Ngữ nghĩa (Semantic Chunking)

Thay vì chia văn bản theo kích thước cố định, hệ thống sử dụng kỹ thuật **phân đoạn ngữ nghĩa (semantic chunking)** để chia tài liệu thành các đoạn nhỏ dựa trên ý nghĩa.

**Nguyên lý Semantic Chunking**:
- Phân tích độ tương đồng ngữ nghĩa giữa các câu và đoạn văn
- Xác định các điểm chia tự nhiên dựa trên sự thay đổi về chủ đề hoặc ý nghĩa
- Đảm bảo mỗi đoạn (chunk) là một đơn vị tri thức hoàn chỉnh về mặt ngữ nghĩa

**Quy trình Thực hiện**:
1. Chuyển đổi các câu thành vector embedding sử dụng mô hình ngôn ngữ
2. Tính toán độ tương đồng ngữ nghĩa giữa các câu liên tiếp
3. Xác định các điểm chia khi độ tương đồng giảm xuống dưới ngưỡng nhất định
4. Tạo các chunk với kích thước tối thiểu để đảm bảo chứa đủ thông tin

**Lợi ích**:
- Giữ nguyên ngữ nghĩa: Mỗi chunk chứa nội dung có ý nghĩa hoàn chỉnh, không bị cắt ngang giữa các ý tưởng
- Tối ưu cho tìm kiếm: Khi truy vấn, hệ thống có thể tìm được các đoạn văn liên quan một cách chính xác hơn
- Hỗ trợ xây dựng sơ đồ tư duy: Các chunk có ngữ nghĩa rõ ràng giúp việc nhóm và tổ chức nội dung chính xác hơn

### 4.4. Biểu diễn Ngữ nghĩa (Embedding Representation)

Mỗi chunk văn bản được chuyển đổi thành một vector embedding - một biểu diễn số học đa chiều của nội dung. Vector embedding được tạo bằng mô hình biểu diễn ngôn ngữ (language model) được huấn luyện sẵn.

**Đặc điểm của Embedding**:
- **Đa chiều**: Mỗi vector có hàng trăm chiều, mỗi chiều đại diện cho một khía cạnh ngữ nghĩa
- **Tương đồng ngữ nghĩa**: Các văn bản có nghĩa tương tự sẽ có các vector gần nhau trong không gian vector
- **Đa ngôn ngữ**: Mô hình được huấn luyện trên nhiều ngôn ngữ, hỗ trợ tốt cho tiếng Việt và tiếng Anh

**Ứng dụng**:
- Lưu trữ trong chỉ mục vector để tìm kiếm nhanh
- So sánh ngữ nghĩa giữa các chunk và câu hỏi của người dùng
- Nhóm các chunk có nội dung liên quan để xây dựng sơ đồ tư duy

Quá trình embedding được thực hiện theo batch để tối ưu tốc độ xử lý, đặc biệt quan trọng khi xử lý tài liệu lớn với hàng trăm hoặc hàng nghìn chunk.

### 4.5. Lưu trữ Video QR Code

Đây là giai đoạn đặc biệt và là đóng góp chính của nghiên cứu: mã hóa tri thức vào QR Code và đóng gói thành video.

#### 4.5.1. Mã hóa Tri thức vào QR Code

Mỗi chunk văn bản, cùng với metadata liên quan, được mã hóa thành một QR Code:

**Cấu trúc Dữ liệu trong QR Code**:
- **Metadata**: Thông tin về chunk như parent_id (ID của chunk cha nếu chunk được chia nhỏ), order (thứ tự trong chuỗi), video_name (tên video chứa chunk này), timestamp (thời điểm tạo)
- **Nội dung Văn bản**: Toàn bộ nội dung của chunk

**Xử lý Chunk Dài**:
- QR Code có giới hạn dung lượng (khoảng 2300 ký tự cho tiếng Việt/Anh với error correction level L)
- Nếu chunk vượt quá giới hạn, hệ thống tự động chia chunk thành các sub-chunk nhỏ hơn
- Mỗi sub-chunk được đánh số thứ tự và liên kết với chunk cha thông qua parent_id
- Khi giải mã, các sub-chunk được ghép lại để tái tạo nội dung gốc

**Tối ưu hóa**:
- Sử dụng error correction level phù hợp để đảm bảo khả năng đọc ngay cả khi QR Code bị hư hỏng nhẹ
- Tự động điều chỉnh version của QR Code dựa trên độ dài dữ liệu
- Xử lý song song (parallel processing) để tạo nhiều QR Code đồng thời, tăng tốc độ xử lý

#### 4.5.2. Đóng gói thành Video

Các QR Code được sắp xếp tuần tự và đóng gói thành một file video MP4:

**Quy trình Tạo Video**:
1. Mỗi QR Code được chuyển đổi thành một frame hình ảnh có kích thước chuẩn (768x768 pixels)
2. Các frame được sắp xếp theo thứ tự logic của nội dung (theo thứ tự chunk trong tài liệu)
3. Video được tạo với frame rate phù hợp (1 frame/giây) để dễ dàng quét hoặc giải mã
4. Video được lưu với tên file duy nhất, bao gồm tên tài liệu gốc và timestamp

**Vai trò của Video QR Code**:
- **Lưu trữ Offline**: Video có thể được lưu trữ trên bất kỳ thiết bị nào, không cần kết nối internet
- **Sao lưu Dài hạn**: Video là định dạng phổ biến, được hỗ trợ rộng rãi, đảm bảo khả năng truy cập trong tương lai
- **Truyền tải Dễ dàng**: Video có thể được chia sẻ qua email, USB, hoặc các phương tiện khác mà không phụ thuộc vào nền tảng cụ thể
- **Bảo toàn Dữ liệu**: QR Code có khả năng chống lỗi (error correction), đảm bảo dữ liệu có thể được khôi phục ngay cả khi một phần video bị hư hỏng

**Tái tạo Tri thức**:
- Khi cần, hệ thống có thể quét hoặc giải mã video để tái tạo lại toàn bộ nội dung và cấu trúc tri thức
- Metadata trong mỗi QR Code cho phép hệ thống khôi phục lại mối quan hệ giữa các chunk
- Toàn bộ cây tri thức có thể được tái tạo từ video QR Code

#### 4.5.3. Lưu trữ và Quản lý

Video QR Code được lưu trữ trong hệ thống file, với các đặc điểm:
- Mỗi tài liệu có một hoặc nhiều video tương ứng (nếu tài liệu rất dài)
- Video được liên kết với metadata trong hệ thống quản lý
- Khi người dùng xóa tài liệu, toàn bộ video liên quan cũng được xóa để tránh dữ liệu mồ côi

### 4.6. Xây dựng Chỉ mục Vector (Vector Index)

Song song với việc tạo video QR Code, hệ thống xây dựng chỉ mục vector để hỗ trợ tìm kiếm nhanh:

**Cấu trúc Chỉ mục**:
- Sử dụng cấu trúc dữ liệu tối ưu cho tìm kiếm tương tự (similarity search)
- Mỗi vector embedding được liên kết với metadata tương ứng (nội dung văn bản, thông tin nguồn, vị trí trong video)

**Chức năng**:
- Cho phép tìm kiếm nhanh các chunk có nội dung liên quan đến câu hỏi
- Hỗ trợ truy vấn ngữ nghĩa thay vì tìm kiếm từ khóa
- Đảm bảo tốc độ phản hồi nhanh ngay cả với số lượng lớn chunk

## 5. MÔ HÌNH SƠ ĐỒ TƯ DUY VÀ TRUY VẤN TRI THỨC

### 5.1. Xây dựng Sơ đồ Tư duy (Memory Tree)

Sau khi các chunk đã được lưu trữ trong video QR Code và chỉ mục vector, hệ thống bắt đầu xây dựng sơ đồ tư duy (Memory Tree) - một cấu trúc cây phản ánh mối quan hệ logic giữa các phần nội dung.

#### 5.1.1. Cấu trúc Phân cấp

Memory Tree được tổ chức theo ba cấp độ:

**Cấp Tài liệu (Document Level)**:
- Node gốc đại diện cho toàn bộ tài liệu
- Chứa tóm tắt tổng quan về nội dung tài liệu
- Liên kết đến tất cả các chunk trong tài liệu
- Có các con (children) là các node cấp section

**Cấp Phần/Chương (Section Level)**:
- Mỗi node đại diện cho một phần nội dung trong tài liệu
- Chứa tiêu đề và tóm tắt của phần đó
- Liên kết đến các chunk thuộc phần đó
- Được phân loại theo mục đích: definition (định nghĩa), procedure (quy trình), argument (lập luận), comparison (so sánh), hoặc reference (tham khảo)

**Cấp Khái niệm (Concept Level)**:
- Có thể được mở rộng trong tương lai để đại diện cho các khái niệm cụ thể
- Cho phép phân tích sâu hơn về nội dung tài liệu

#### 5.1.2. Quy trình Xây dựng

**Bước 1: Nhóm Chunk thành Section**:
- Phân tích các chunk và nhóm chúng thành các phần dựa trên ngữ nghĩa và cấu trúc logic
- Sử dụng embedding để tính toán độ tương đồng giữa các chunk
- Xác định các nhóm chunk có nội dung liên quan mật thiết

**Bước 2: Tạo Document Node**:
- Tóm tắt toàn bộ nội dung tài liệu sử dụng mô hình ngôn ngữ lớn (LLM)
- Phân loại mục đích của tài liệu (definition, procedure, argument, comparison, reference)
- Tạo vector embedding cho tóm tắt để hỗ trợ tìm kiếm ở cấp document

**Bước 3: Tạo Section Nodes**:
- Với mỗi section, tạo một node chứa:
  - Tiêu đề của section (tự động sinh hoặc trích xuất từ nội dung)
  - Tóm tắt nội dung section (sử dụng LLM)
  - Vector embedding của tóm tắt
  - Liên kết đến các chunk thuộc section đó
  - Phân loại mục đích (intent type)
- Thiết lập mối quan hệ cha-con với document node

**Bước 4: Xây dựng Memory Index**:
- Tạo một chỉ mục vector riêng cho các node trong Memory Tree
- Cho phép tìm kiếm ở mức độ cao hơn (document/section level) thay vì chỉ ở mức chunk
- Hỗ trợ truy vấn với ngữ cảnh sâu hơn

#### 5.1.3. Lợi ích của Memory Tree

- **Dễ ghi nhớ**: Cấu trúc cây giúp người học dễ dàng nắm bắt tổng quan và mối quan hệ giữa các phần
- **Nhìn thấy tổng quan**: Người học có thể xem toàn bộ cấu trúc kiến thức một cách trực quan
- **Học theo cấu trúc**: Thay vì đọc tuyến tính, người học có thể điều hướng theo cấu trúc logic
- **Hỗ trợ truy vấn**: Memory Tree giúp hệ thống hiểu ngữ cảnh và trả lời câu hỏi chính xác hơn

### 5.2. Sinh Sơ đồ Tư duy (Mind Map Generation)

Ngoài Memory Tree được xây dựng tự động, hệ thống còn hỗ trợ sinh sơ đồ tư duy (Mind Map) theo yêu cầu của người dùng:

**Quy trình Sinh Mind Map**:
1. Người dùng chọn một hoặc nhiều tài liệu
2. Hệ thống phân tích nội dung và xác định các chủ đề chính
3. Sử dụng LLM để tạo cấu trúc cây với các nhánh và nút
4. Hiển thị sơ đồ tư duy dưới dạng trực quan

**Đặc điểm**:
- Có thể được cập nhật hoặc tái sinh khi dữ liệu thay đổi
- Hỗ trợ nhiều chiến lược sinh khác nhau (iterative, semantic, coreference)
- Cho phép người dùng tùy chỉnh và lưu lại để sử dụng sau

**Ứng dụng**:
- Ôn tập nhanh trước kỳ thi
- Tổng hợp kiến thức từ nhiều tài liệu
- Trình bày và chia sẻ kiến thức

### 5.3. Cơ chế Truy vấn Tri thức

Hệ thống hỗ trợ truy vấn tri thức thông minh dựa trên ngữ nghĩa, vượt trội so với tìm kiếm từ khóa truyền thống.

#### 5.3.1. Phân loại Câu hỏi (Query Classification)

Trước khi thực hiện truy vấn, hệ thống tự động phân loại câu hỏi của người dùng:

- **Overview**: Câu hỏi tổng quan như "Tài liệu này nói về gì?"
- **Main_points**: Câu hỏi về các ý chính
- **Detail**: Câu hỏi yêu cầu chi tiết về một phần cụ thể
- **How**: Câu hỏi về cách làm, quy trình
- **Why**: Câu hỏi về lý do, nguyên nhân
- **Compare**: Câu hỏi so sánh, đối chiếu
- **Locate**: Câu hỏi tìm vị trí trong tài liệu
- **Fact**: Câu hỏi về sự thật, thông tin cụ thể

Phân loại này giúp hệ thống chọn chiến lược truy vấn phù hợp và điều chỉnh cách trả lời.

#### 5.3.2. Truy vấn Ngữ nghĩa (Semantic Search)

**Quy trình Truy vấn**:

1. **Embedding câu hỏi**: Câu hỏi của người dùng được chuyển đổi thành vector embedding sử dụng cùng mô hình với các chunk

2. **Routing thông minh**: Dựa trên loại câu hỏi, hệ thống quyết định:
   - Tìm kiếm ở cấp độ nào (document, section, hoặc chunk)
   - Số lượng kết quả cần lấy (top_k)
   - Độ ưu tiên cho các loại node khác nhau

3. **Tìm kiếm trong Memory Tree** (nếu có):
   - Tìm kiếm trong memory index để tìm các node (document hoặc section) liên quan
   - Lọc theo nguồn tài liệu được chọn (nếu có)
   - Lấy các node có độ tương đồng cao nhất

4. **Thu thập Evidence**:
   - Từ các node tìm được, lấy danh sách chunk references
   - Load nội dung các chunk này từ chunk index
   - Sắp xếp và lọc để có context phù hợp

5. **Xây dựng Context**:
   - Kết hợp tóm tắt từ các node (summary) và trích đoạn từ các chunk (snippets)
   - Tạo "narrative glue" - đoạn văn ngắn nối logic giữa summary và evidence
   - Giới hạn độ dài context dựa trên loại câu hỏi

6. **Sinh câu trả lời**:
   - Sử dụng LLM để sinh câu trả lời dựa trên context
   - Câu trả lời được tạo theo phong cách tự nhiên, như một người đã đọc tài liệu đang giải thích lại
   - Không lộ các chi tiết kỹ thuật

7. **Fallback**: Nếu không tìm được kết quả trong Memory Tree, hệ thống tự động chuyển sang tìm kiếm ở mức chunk

**Ưu điểm so với Tìm kiếm Từ khóa**:
- **Hiểu ngữ nghĩa**: Tìm được nội dung liên quan ngay cả khi không có từ khóa chính xác
- **Xử lý từ đồng nghĩa**: Các từ có nghĩa tương tự được nhận diện tự động
- **Hiểu ngữ cảnh**: Phân biệt các nghĩa khác nhau của cùng một từ dựa trên ngữ cảnh
- **Hỗ trợ đa ngôn ngữ**: Tìm kiếm hiệu quả với tài liệu tiếng Việt và tiếng Anh

#### 5.3.3. Truy vấn Hai Tầng

Hệ thống hỗ trợ hai mức độ truy vấn:

**Truy vấn Chunk-level**:
- Tìm kiếm trực tiếp trong các chunk đã được lưu trữ
- Phù hợp cho các câu hỏi chi tiết, cụ thể
- Có thể sử dụng ngay sau khi video QR Code đã được tạo

**Truy vấn Memory-level**:
- Tìm kiếm trong Memory Tree, bắt đầu từ document hoặc section nodes
- Phù hợp cho các câu hỏi tổng quan, yêu cầu hiểu cấu trúc tài liệu
- Cung cấp ngữ cảnh sâu hơn và câu trả lời có cấu trúc tốt hơn
- Chỉ có thể sử dụng sau khi Memory Tree đã được xây dựng

Hệ thống tự động chọn mức độ truy vấn phù hợp dựa trên trạng thái hiện tại của tài liệu và loại câu hỏi.

## 6. XỬ LÝ BẤT ĐỒNG BỘ VÀ TRẢI NGHIỆM NGƯỜI DÙNG

### 6.1. Mô hình Xử lý Hai Pha

Để đảm bảo trải nghiệm người dùng tốt, hệ thống áp dụng mô hình xử lý hai pha:

**Pha 1 - Phản hồi Nhanh (Critical Path)**:
- Trích xuất nội dung văn bản từ tài liệu
- Phân đoạn văn bản theo ngữ nghĩa
- Tạo video QR Code
- Biểu diễn nội dung dưới dạng embedding vector
- Lưu trữ vào hệ thống tìm kiếm vector

Sau khi hoàn thành pha này, hệ thống đánh dấu trạng thái "index_ready", cho phép người dùng:
- Bắt đầu truy vấn ở mức độ đoạn văn bản (chunk-level)
- Xem video QR Code đã được tạo
- Sử dụng các chức năng cơ bản của hệ thống

**Pha 2 - Xử lý Nền (Background Processing)**:
- Xây dựng cấu trúc tri thức dạng cây (Memory Tree)
- Tạo các node đại diện cho tài liệu và các phần nội dung
- Tóm tắt và phân loại nội dung theo mục đích
- Xây dựng memory index cho truy vấn ở mức cao hơn

Sau khi hoàn thành, hệ thống đánh dấu trạng thái "ready", cho phép:
- Truy vấn ở mức độ cao hơn (memory-level) với ngữ cảnh sâu hơn
- Sinh sơ đồ tư duy với chất lượng tốt hơn
- Sử dụng đầy đủ các tính năng của hệ thống

Mô hình này đảm bảo người dùng có thể bắt đầu sử dụng hệ thống ngay sau khi pha 1 hoàn thành, trong khi pha 2 tiếp tục chạy nền để nâng cao chất lượng.

### 6.2. Theo dõi Tiến trình

Hệ thống duy trì một registry (sổ đăng ký) theo dõi trạng thái của từng tài liệu:

**Thông tin Theo dõi**:
- **Trạng thái hiện tại**: processing (đang xử lý), index_ready (đã sẵn sàng cho truy vấn chunk-level), ready (đã sẵn sàng đầy đủ), hoặc error (lỗi)
- **Tiến trình xử lý**: Phần trăm hoàn thành (0.0 - 1.0)
- **Trạng thái phụ (substatus)**: faiss_ready (chỉ mục vector đã sẵn sàng), building_memory_tree (đang xây dựng Memory Tree), memory_tree_ready (Memory Tree đã sẵn sàng)
- **Khả năng truy vấn**: chunk_query (có thể truy vấn ở mức chunk), memory_query (có thể truy vấn ở mức memory)

**Cơ chế Cập nhật**:
- Frontend có thể polling endpoint trạng thái để cập nhật tiến trình xử lý theo thời gian thực
- Hiển thị cho người dùng biết tài liệu đang được xử lý đến đâu
- Cung cấp phản hồi trực quan về tiến trình

### 6.3. Truy vấn Từng Phần

Người dùng có thể bắt đầu truy vấn ngay khi tài liệu đạt trạng thái "index_ready", mặc dù Memory Tree có thể chưa hoàn thành:

- Hệ thống tự động chọn phương thức truy vấn phù hợp dựa trên trạng thái hiện tại
- Nếu Memory Tree chưa sẵn sàng, hệ thống sử dụng truy vấn chunk-level
- Khi Memory Tree hoàn thành, hệ thống tự động nâng cấp lên truy vấn memory-level cho các câu hỏi phù hợp
- Người dùng nhận được thông báo khi có thể sử dụng các tính năng nâng cao hơn

## 7. QUẢN LÝ VÀ ĐỒNG BỘ DỮ LIỆU

### 7.1. Đảm bảo Tính nhất quán

Hệ thống duy trì tính nhất quán giữa các thành phần dữ liệu:

**Source Registry**:
- Một registry trung tâm theo dõi tất cả các tài liệu đã được tải lên
- Lưu trữ trạng thái xử lý, tiến trình, và metadata liên quan
- Sử dụng file locking để tránh race condition trong môi trường đa luồng

**Đồng bộ Metadata**:
- Metadata của chunk (trong chunk index) và metadata của node (trong memory index) được đồng bộ
- Đảm bảo thông tin về nguồn tài liệu luôn nhất quán
- Liên kết giữa video QR Code, chunk index, và memory index được duy trì chính xác

### 7.2. Chiến lược Xóa Dữ liệu Sạch (Clean Delete)

Khi người dùng xóa một tài liệu, hệ thống thực hiện xóa toàn bộ dữ liệu liên quan một cách có hệ thống:

**Quy trình Xóa**:
1. **Xóa file gốc**: Xóa file tài liệu gốc trong thư mục input
2. **Xóa video QR Code**: Xóa tất cả các file video QR Code đã được tạo cho tài liệu đó
3. **Xóa chunk index**: 
   - Xóa tất cả các chunk thuộc về tài liệu đó khỏi chunk metadata
   - Rebuild chunk index từ metadata còn lại
   - Đảm bảo không còn vector nào trong index thuộc về tài liệu đã xóa
4. **Xóa Memory Tree**:
   - Xóa tất cả các node (document và section) thuộc về tài liệu đó
   - Rebuild memory index từ các tree còn lại
   - Đảm bảo không còn node nào trong memory index thuộc về tài liệu đã xóa
5. **Xóa registry entry**: Xóa entry trong source registry

**Đảm bảo An toàn**:
- Quá trình xóa được thực hiện theo thứ tự để đảm bảo không có dữ liệu mồ côi (orphaned data) còn sót lại
- Nếu có lỗi xảy ra, hệ thống có cơ chế rollback để khôi phục trạng thái trước đó
- Backup các file quan trọng trước khi xóa để có thể khôi phục nếu cần

### 7.3. Rebuild Index

Sau khi xóa hoặc cập nhật dữ liệu, hệ thống tự động rebuild các index để đảm bảo tính nhất quán:

**Chunk Index Rebuild**:
- Tạo lại chunk index từ metadata còn lại
- Đảm bảo ID của chunk được giữ nguyên
- Sử dụng batch embedding để tối ưu tốc độ rebuild

**Memory Index Rebuild**:
- Tạo lại memory index từ các Memory Tree còn lại
- Đảm bảo tất cả các node đều có trong index
- Đồng bộ với memory_trees.json

Quá trình rebuild được tối ưu bằng batch processing để đảm bảo tốc độ nhanh ngay cả với số lượng lớn dữ liệu.

## 8. ĐÁNH GIÁ VÀ KHẢ NĂNG ỨNG DỤNG

### 8.1. Ưu điểm của Hệ thống

**Về Phương pháp Lưu trữ**:
- **Video QR Code**: Một phương pháp lưu trữ tri thức mới, không phụ thuộc nền tảng, dễ chia sẻ và bảo toàn dài hạn
- **Bền vững**: Video là định dạng phổ biến, được hỗ trợ rộng rãi, đảm bảo khả năng truy cập trong tương lai
- **Offline**: Có thể lưu trữ và sử dụng mà không cần kết nối internet

**Về Tổ chức Tri thức**:
- **Sơ đồ tư duy tự động**: Giúp người học dễ dàng nắm bắt tổng quan và mối quan hệ giữa các phần
- **Cấu trúc cây**: Phản ánh logic của tài liệu, hỗ trợ học tập theo cấu trúc thay vì đọc tuyến tính
- **Memory Tree**: Cung cấp ngữ cảnh sâu cho truy vấn, cải thiện chất lượng câu trả lời

**Về Truy vấn**:
- **Truy vấn ngữ nghĩa**: Vượt trội so với tìm kiếm từ khóa truyền thống, hiểu được ý nghĩa của câu hỏi
- **Routing thông minh**: Tự động chọn chiến lược truy vấn phù hợp với từng loại câu hỏi
- **Hai tầng truy vấn**: Hỗ trợ cả truy vấn chi tiết (chunk-level) và truy vấn tổng quan (memory-level)

**Về Trải nghiệm Người dùng**:
- **Xử lý bất đồng bộ**: Không chặn giao diện, cho phép người dùng sử dụng sớm
- **Theo dõi tiến trình**: Phản hồi trực quan về trạng thái xử lý
- **Giao diện đơn giản**: Dễ sử dụng, không yêu cầu kiến thức kỹ thuật

### 8.2. Khả năng Ứng dụng

Hệ thống có thể được áp dụng trong nhiều ngữ cảnh:

**Giáo dục**:
- **Hỗ trợ sinh viên**: Đọc và hiểu tài liệu học tập dài, ôn tập nhanh trước kỳ thi
- **Hỗ trợ giảng viên**: Tổ chức và quản lý tài liệu giảng dạy, tạo sơ đồ tư duy cho bài giảng
- **Nghiên cứu**: Xử lý và phân tích tài liệu nghiên cứu, tổng hợp thông tin từ nhiều nguồn

**Thư viện Số**:
- Lưu trữ và tổ chức tài liệu số
- Hỗ trợ tìm kiếm và truy xuất thông tin
- Bảo toàn tri thức dài hạn

**Giáo dục Thông minh**:
- Tích hợp vào hệ thống học tập trực tuyến
- Hỗ trợ học tập cá nhân hóa
- Cung cấp phản hồi thông minh cho người học

**Lưu trữ Tri thức Dài hạn**:
- Lưu trữ tri thức quan trọng dưới dạng video QR Code
- Đảm bảo khả năng truy cập trong tương lai
- Chia sẻ tri thức giữa các thế hệ

### 8.3. Hạn chế và Hướng Phát triển

**Hạn chế hiện tại**:
- Phụ thuộc vào chất lượng của mô hình embedding và LLM
- Xử lý tài liệu rất dài có thể mất thời gian
- Memory Tree hiện tại chỉ có hai cấp (document và section), chưa có concept level đầy đủ
- Video QR Code có giới hạn dung lượng, cần chia nhỏ chunk lớn

**Hướng phát triển**:
- **Mở rộng Memory Tree**: Thêm nhiều cấp độ hơn (topic, concept, entity) để phân tích sâu hơn
- **Tối ưu Video QR Code**: Nén dữ liệu hiệu quả hơn, hỗ trợ chunk lớn hơn
- **Truy vấn đa tài liệu**: So sánh nội dung giữa các tài liệu, tổng hợp từ nhiều nguồn
- **Visualization**: Tích hợp các công cụ visualization để hiển thị cấu trúc Memory Tree và Mind Map
- **Xử lý đa phương tiện**: Cải thiện khả năng xử lý tài liệu có nhiều hình ảnh, bảng biểu, công thức toán học
- **Hỗ trợ nhiều ngôn ngữ**: Mở rộng hỗ trợ cho nhiều ngôn ngữ hơn
- **Tối ưu hiệu suất**: Cải thiện tốc độ xử lý cho tài liệu rất lớn (hàng trăm trang)
- **Tích hợp với hệ thống khác**: Kết nối với các hệ thống quản lý học tập (LMS), thư viện số

## 9. KẾT LUẬN VÀ HƯỚNG PHÁT TRIỂN

Nghiên cứu này đã trình bày một hệ thống lưu trữ và truy vấn tri thức từ tài liệu học thuật, kết hợp các kỹ thuật trí tuệ nhân tạo với phương pháp lưu trữ mới dựa trên Video QR Code. Hệ thống được thiết kế để giải quyết các thách thức trong việc lưu trữ, tổ chức và truy xuất tri thức từ tài liệu học thuật.

**Đóng góp chính của nghiên cứu**:

1. **Mô hình lưu trữ tri thức mới**: Đề xuất sử dụng Video QR Code như một phương pháp lưu trữ tri thức bền vững, không phụ thuộc nền tảng, có thể chia sẻ và bảo toàn dài hạn. Mỗi video QR Code không chỉ chứa dữ liệu thô mà còn chứa cấu trúc tri thức và metadata, cho phép tái tạo lại toàn bộ cây tri thức khi cần.

2. **Tự động sinh sơ đồ tư duy**: Sử dụng AI để tự động phân tích nội dung và xây dựng cấu trúc cây (Memory Tree) phản ánh mối quan hệ logic giữa các phần. Sơ đồ tư duy giúp người học dễ dàng nắm bắt tổng quan, ghi nhớ cấu trúc và học tập hiệu quả hơn.

3. **Truy vấn ngữ nghĩa**: Hỗ trợ tìm kiếm dựa trên ý nghĩa thay vì từ khóa chính xác, cho phép người dùng đặt câu hỏi tự nhiên và nhận được câu trả lời có ngữ cảnh. Hệ thống tự động phân loại câu hỏi và chọn chiến lược truy vấn phù hợp.

4. **Xử lý bất đồng bộ**: Mô hình xử lý hai pha cho phép người dùng sử dụng hệ thống sớm trong khi chất lượng tiếp tục được cải thiện ở background, đảm bảo trải nghiệm người dùng tốt.

**Khả năng ứng dụng**:

Hệ thống có tiềm năng ứng dụng rộng rãi trong giáo dục, nghiên cứu, thư viện số và lưu trữ tri thức dài hạn. Với việc tiếp tục cải thiện và mở rộng, hệ thống có thể trở thành một công cụ hữu ích cho việc xử lý, tổ chức và truy xuất thông tin từ tài liệu học thuật.

**Hướng phát triển tương lai**:

- Nghiên cứu và triển khai các kỹ thuật nâng cao hơn cho semantic chunking và embedding
- Mở rộng Memory Tree với nhiều cấp độ phân tích sâu hơn (topic, concept, entity)
- Tối ưu hóa phương pháp lưu trữ Video QR Code để hỗ trợ chunk lớn hơn và hiệu quả hơn
- Phát triển khả năng xử lý đa phương tiện (hình ảnh, bảng biểu, công thức toán học)
- Tích hợp các công nghệ visualization để hiển thị cấu trúc tri thức
- Tối ưu hiệu suất và khả năng mở rộng cho hệ thống lớn
- Đánh giá và so sánh với các hệ thống tương tự để cải thiện chất lượng
- Phát triển các ứng dụng thực tế trong giáo dục và nghiên cứu

---

**Tài liệu tham khảo** (cần bổ sung):
- Các nghiên cứu về semantic search và vector embeddings
- Các nghiên cứu về knowledge representation và memory structures
- Các nghiên cứu về document understanding và question answering systems
- Các nghiên cứu về QR Code và phương pháp lưu trữ dữ liệu
